"""Offline accessibility ablations. Production facade-rank-1 stays unchanged.

python -m city_sim.facade_access_experiment --output out/facade-access-study
"""
import argparse
from collections import Counter,defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
import time
import numpy as np
from shapely import STRtree
from shapely.geometry import Point,LineString,box,shape
from shapely.ops import transform,nearest_points,unary_union
from weather_coverage.geography import TO_METRIC,TO_WGS84,tags
from .api import make_app
from .object_registry import ObjectRegistry
from .facade_inference import FacadeInference,fold_of


def building_group(reg,bid):
    b=reg.buildings[bid]['properties'];kind=b.get('building_type')
    key=bid.split(':')[1][0]+bid.split(':')[2] if bid.startswith('osm:') else ''
    meta=tags(reg.catalog_city_elements[key]) if key in reg.catalog_city_elements else {}
    if kind in {'apartments','house','residential','dormitory','detached','terrace','semidetached_house'}:
        return 'residential'
    if kind in {'retail','commercial','office','supermarket','kiosk'} or meta.get('shop') or meta.get('office'):
        return 'commercial'
    return 'other_or_unknown'


def fit(x,y,weights):
    x=np.column_stack([np.ones(len(x)),x]);w=weights/weights.sum();beta=np.zeros(x.shape[1])
    for _ in range(400):
        pred=1/(1+np.exp(-np.clip(x@beta,-30,30)))
        penalty=.01*beta;penalty[0]=0
        beta-=1.5*(x.T@(w*(pred-y))+penalty)
    return beta


class AccessFeatures:
    def __init__(self, inference, city):
        self.inference=inference;self.city=city;reg=inference.registry;cat=reg.catalog
        self.names=['nearest_distance','nearest_foot_distance','nearest_service_distance','nearest_street_distance',
            'log_wall_length','relative_wall_length','open_space','log_area','elongation',
            'nearest_is_foot','nearest_is_service','nearest_is_street','nearest_is_steps','nearest_is_parking_aisle',
            'outward_path_alignment','approach_crosses_other_building','approach_crosses_barrier',
            'approach_has_steps_nearby','approach_has_barrier_nearby','shared_building_road_node',
            'shared_pedestrian_node','shared_service_node','nearest_component_street_anchored',
            'residential_x_relative_length','commercial_x_relative_length','residential_x_foot_distance','commercial_x_street_distance',
            'coarse_dsm_delta','coarse_dsm_abs_grade','terrain_supported_60m','terrain_missing_or_sub60m',
            'raw_dsm_delta','raw_dsm_abs_grade']
        # Only the unchanging OSM geometry is used. Entrance/door tags are never features.
        barrier_geoms=[];steps=[];obstacle_counts=Counter()
        for key,e in reg.catalog_city_elements.items():
            meta=tags(e)
            if key.startswith('n') and meta.get('barrier'):
                barrier_geoms.append(transform(TO_METRIC,Point(float(e.get('lon')),float(e.get('lat')))))
                obstacle_counts['barrier_nodes']+=1
            if key.startswith('w') and (meta.get('barrier') or meta.get('man_made')=='retaining_wall'):
                refs=['n'+n.get('ref') for n in e.findall('nd')]
                if len(refs)>=2 and all(n in reg.catalog_city_elements for n in refs):
                    line=LineString([(float(reg.catalog_city_elements[n].get('lon')),float(reg.catalog_city_elements[n].get('lat'))) for n in refs])
                    barrier_geoms.append(transform(TO_METRIC,line));obstacle_counts['barrier_or_retaining_ways']+=1
        for f,g in zip(cat.roads.features,cat.metric_roads):
            if f['properties']['highway']=='steps':steps.append(g)
        self.obstacle_counts={**obstacle_counts,'steps_ways':len(steps)}
        barriers=STRtree(barrier_geoms);stairtree=STRtree(steps)
        eligible=[i for i,f in enumerate(cat.roads.features) if f['properties'].get('foot') not in {'no','private'}
            and f['properties'].get('access') not in {'no','private'} and f['properties']['highway'] not in {'trunk','motorway','trunk_link'}]
        roads=[cat.metric_roads[i] for i in eligible];roadtree=STRtree(roads)
        self.values={};self.evidence={};self.groups={};terrain_records=[]
        for bid in sorted(inference.labels):
            group=building_group(reg,bid);self.groups[bid]=group;rows=[];records=[]
            building=transform(TO_METRIC,shape(reg.buildings[bid]['geometry']))
            key=bid.split(':')[1][0]+bid.split(':')[2] if bid.startswith('osm:') else ''
            element=reg.catalog_city_elements.get(key)
            refs=['n'+n.get('ref') for n in element.findall('nd')] if element is not None and element.tag=='way' else []
            shared=[]
            for node in refs:
                if node in cat.membership and node in reg.catalog_city_elements:
                    e=reg.catalog_city_elements[node];point=transform(TO_METRIC,Point(float(e.get('lon')),float(e.get('lat'))))
                    shared.append((node,point,cat.membership[node]))
            for r in inference.rows[bid]:
                wall=r['wall'];mid=wall.interpolate(.5,normalized=True)
                idx=int(roadtree.nearest(wall));road=cat.roads.features[eligible[idx]];props=road['properties'];roadgeom=roads[idx]
                anchor,target=nearest_points(wall,roadgeom);gap=anchor.distance(target)
                link=LineString([anchor,target]) if gap>.001 else mid.buffer(.001).boundary
                blockers=cat.building_index.query(link,predicate='intersects')
                blocked=any(cat.building_ids[j]!=bid and cat.metric_buildings[j].intersection(link).length>.1 for j in blockers)
                barrier_cross=bool(len(barriers.query(link.buffer(.3),predicate='intersects')))
                steps_near=bool(len(stairtree.query(link.buffer(5),predicate='intersects')))
                barrier_near=bool(len(barriers.query(mid.buffer(15),predicate='intersects')))
                nx,ny=r['normal'];align=((target.x-anchor.x)*nx+(target.y-anchor.y)*ny)/max(1.,gap)
                matches=[(node,ids) for node,p,ids in shared if wall.distance(p)<1]
                ids={rid for _,rs in matches for rid in rs}
                road_by_id={cat.roads.features[i]['id']:cat.roads.features[i]['properties'] for i in range(len(cat.roads.features))} if matches else {}
                foot=any(road_by_id[rid]['mode']=='pedestrian' for rid in ids)
                service=any(road_by_id[rid]['highway']=='service' for rid in ids)
                lon1,lat1=TO_WGS84(anchor.x+nx*2,anchor.y+ny*2);lon2,lat2=TO_WGS84(target.x,target.y)
                z=self.city.terrain.sample(np.array([lon1,lon2]),np.array([lat1,lat2]))
                raw_delta=float(z[1]-z[0]) if np.isfinite(z).all() else None
                # DSM cannot resolve a doorstep. Use a 60 m minimum support only
                # as a coarse neighbourhood contrast, never engineering grade.
                supported=raw_delta is not None and gap>=60
                delta=raw_delta if supported else 0.
                coarse_grade=abs(delta)/max(gap,60)
                rawgrade=abs(raw_delta)/max(gap,5) if raw_delta is not None else 0.
                ds=r['distances_m'];x=r['x']
                v=[min(gap,100)/100,*[d/100 for d in ds],x[0],x[1],x[5],x[6],x[7],
                   float(props['mode']=='pedestrian'),float(props['highway']=='service'),float(props['mode']=='vehicle' and props['highway']!='service'),
                   float(props['highway']=='steps'),float(props.get('service')=='parking_aisle'),align,float(blocked),float(barrier_cross),
                   float(steps_near),float(barrier_near),float(bool(matches)),float(foot),float(service),float(props['connected_to_street']),
                   float(group=='residential')*x[1],float(group=='commercial')*x[1],float(group=='residential')*ds[0]/100,
                   float(group=='commercial')*ds[2]/100,np.clip(delta/30,-3,3),min(coarse_grade,2),float(supported),float(not supported),
                   np.clip((raw_delta or 0)/30,-3,3),min(rawgrade,2)]
                assert len(v)==len(self.names)
                rows.append(v)
                # Perturb endpoints by one native grid spacing to assess stability.
                perturbed=[]
                for dx,dy in ((30,0),(-30,0),(0,30),(0,-30)):
                    ll1=TO_WGS84(anchor.x+nx*2+dx,anchor.y+ny*2+dy);ll2=TO_WGS84(target.x+dx,target.y+dy)
                    zz=self.city.terrain.sample(np.array([ll1[0],ll2[0]]),np.array([ll1[1],ll2[1]]))
                    if np.isfinite(zz).all():perturbed.append(float(zz[1]-zz[0]))
                record=dict(facade_id=r['id'],nearest_way_id=road['id'],gap_m=gap,nearest_highway=props['highway'],
                    shared_nodes=[node for node,_ in matches],topological_connection=bool(matches),geometric_link_is_graph_edge=False,
                    raw_dsm_delta_m=raw_delta,coarse_terrain_supported=supported,perturbed_deltas_m=perturbed,
                    absolute_delta_span_m=max(perturbed)-min(perturbed) if perturbed else None,
                    obstacle_crossing=blocked or barrier_cross)
                records.append(record);terrain_records.append(record)
            self.values[bid]=np.array(rows);self.evidence[bid]=records
        valid=[r for r in terrain_records if r['raw_dsm_delta_m'] is not None]
        spans=[r['absolute_delta_span_m'] for r in valid if r['absolute_delta_span_m'] is not None]
        self.terrain_quality=dict(source=city.terrain.metadata,facades=len(terrain_records),finite_pairs=len(valid),
            pairs_below_30m=sum(r['gap_m']<30 for r in valid),pairs_below_60m=sum(r['gap_m']<60 for r in valid),
            supported_60m=sum(r['coarse_terrain_supported'] for r in valid),
            one_cell_translation_delta_span_m_quantiles=np.quantile(spans,[.5,.9,.99]).tolist() if spans else [],
            independent_ground_check='not_available',door_elevation_accuracy='not_testable',
            limitation='30m DSM includes structures/vegetation; bilinear samples are not independent doorstep measurements')

    def variants(self):
        # Same outer folds, training objective, iterations and regularization.
        shape=[4,5,6,7,8]
        distance=shape+[0]
        typed=distance+[1,2,3,9,10,11,12,13]
        access=list(range(27))
        terrain=list(range(31))
        return dict(distance_only=([0],False),shape_distance=(distance,False),distance_road_type=(typed,False),
            access_no_terrain_no_neighbours=(access,False),access_no_terrain_neighbours=(access,True),
            access_terrain_no_neighbours=(terrain,False),access_terrain_neighbours=(terrain,True),
            access_raw_dsm_neighbours=(access+[31,32],True),
            access_without_topology=([i for i in access if i not in (19,20,21)],True),
            access_without_obstacles=([i for i in access if i not in (15,16,17,18)],True),
            access_without_building_type=([i for i in access if i not in (23,24,25,26)],True))


def evaluate(infer,features):
    validation=infer.validate();variants=features.variants();predictions=[];folds=[]
    for split in validation['folds']:
        train=split['training_building_ids'];test=split['test_building_ids'];fold=split['fold']
        x=np.vstack([features.values[b] for b in train]);y=np.array([float(j in infer.labels[b]) for b in train for j in range(len(infer.rows[b]))])
        weights=np.array([1/len(infer.rows[b]) for b in train for _ in infer.rows[b]])
        models={name:fit(x[:,cols],y,weights) for name,(cols,_) in variants.items()}
        current=infer.fit(train)
        folds.append(dict(fold=fold,train=train,test=test,coefficients={name:m.tolist() for name,m in models.items()}))
        for bid in test:
            rows=infer.rows[bid];base_current,neighbours=infer.scores(bid,current)
            no_neighbour=np.array([[1.,*r['x']] for r in rows])@current[0]
            vote=base_current-no_neighbour
            scores={'longest_facade':np.array([r['wall'].length for r in rows]),'current':base_current,'current_no_neighbours':no_neighbour}
            for name,(cols,neigh) in variants.items():
                scores[name]=np.column_stack([np.ones(len(rows)),features.values[bid][:,cols]])@models[name]+(vote if neigh else 0)
            methods={}
            for name,values in scores.items():
                rank=sorted(range(len(rows)),key=lambda j:(-values[j],rows[j]['id']))
                methods[name]=dict(top1_hit=rank[0] in infer.labels[bid],top2_recall=len(set(rank[:2])&infer.labels[bid])/len(infer.labels[bid]),
                    selected_facade=rows[rank[0]]['id'])
            predictions.append(dict(building_id=bid,block=infer.blocks[bid],fold=fold,group=features.groups[bid],
                observed_facades=sorted(infer.labels[bid]),methods=methods,neighbour_training_ids=neighbours))
    names=list(predictions[0]['methods']);metrics={};comparisons={};contrasts={}
    pairs=dict(road_type=('distance_road_type','shape_distance'),
        topology=('access_no_terrain_neighbours','access_without_topology'),
        obstacles=('access_no_terrain_neighbours','access_without_obstacles'),
        building_type=('access_no_terrain_neighbours','access_without_building_type'),
        terrain=('access_terrain_no_neighbours','access_no_terrain_no_neighbours'),
        terrain_with_neighbours=('access_terrain_neighbours','access_no_terrain_neighbours'),
        neighbours=('access_no_terrain_neighbours','access_no_terrain_no_neighbours'),
        current_neighbours=('current','current_no_neighbours'),
        raw_dsm=('access_raw_dsm_neighbours','access_no_terrain_neighbours'))
    rng=np.random.default_rng(20261122)
    comparison_count=((len(names)-2)*2+len(pairs))*5
    for group in ('all','residential','commercial','no_shared_nodes','with_shared_nodes'):
        rows=[r for r in predictions if group=='all' or r['group']==group or
            (group=='no_shared_nodes' and not features.values[r['building_id']][:,19].any()) or
            (group=='with_shared_nodes' and features.values[r['building_id']][:,19].any())]
        blocks=sorted({tuple(r['block']) for r in rows})
        metrics[group]=dict(buildings=len(rows),blocks=len(blocks),methods={name:{k:float(np.mean([r['methods'][name][k] for r in rows])) if rows else None
            for k in ('top1_hit','top2_recall')} for name in names})
        comparisons[group]={}
        if not rows:
            continue
        sums=np.array([[sum(float(r['methods'][name]['top1_hit']) for r in rows if tuple(r['block'])==block) for name in names] for block in blocks])
        counts=np.array([sum(tuple(r['block'])==block for r in rows) for block in blocks])
        samples=rng.integers(0,len(blocks),size=(4000,len(blocks)))
        means=sums[samples].sum(axis=1)/counts[samples].sum(axis=1)[:,None]
        contrasts[group]={}
        for label,(name,baseline) in pairs.items():
            diff=means[:,names.index(name)]-means[:,names.index(baseline)]
            contrasts[group][label]=dict(with_feature=name,without_feature=baseline,
                difference=metrics[group]['methods'][name]['top1_hit']-metrics[group]['methods'][baseline]['top1_hit'],
                block_bootstrap_95pct=np.quantile(diff,[.025,.975]).tolist(),
                familywise_bonferroni_interval=np.quantile(diff,[.025/comparison_count,1-.025/comparison_count]).tolist())
        for name in names:
            if name in {'current','longest_facade'}:continue
            comparisons[group][name]={}
            for baseline in ('current','longest_facade'):
                diff=means[:,names.index(name)]-means[:,names.index(baseline)]
                comparisons[group][name][baseline]=dict(difference=metrics[group]['methods'][name]['top1_hit']-metrics[group]['methods'][baseline]['top1_hit'],
                    block_bootstrap_95pct=np.quantile(diff,[.025,.975]).tolist(),
                    familywise_bonferroni_interval=np.quantile(diff,[.025/comparison_count,1-.025/comparison_count]).tolist())
    return dict(metrics=metrics,comparisons=comparisons,feature_contrasts=contrasts,predictions=predictions,folds=folds,
        feature_names=features.names,variants={k:{'features':[features.names[i] for i in cols],'neighbour_vote':vote} for k,(cols,vote) in variants.items()},
        protocol=dict(block_m=500,guard_m=100,outer_folds=5,regularization=.01,iterations=400,bootstrap_replicates=4000,seed=20261122,
            multiplicity_comparisons=comparison_count,tuning='none; exploratory ablations; no production promotion'),
        topology_leakage_warning='Building-road shared nodes use no entrance tags, but OSM path mapping can encode known entrances',
        production_changed=False)


def run(output):
    output.mkdir(parents=True,exist_ok=True);start=time.perf_counter()
    app=make_app(db_path=output/'scratch.sqlite');reg=ObjectRegistry(app.state.city);infer=FacadeInference(reg)
    print('Labelled buildings',len(infer.labels),flush=True)
    features=AccessFeatures(infer,app.state.city)
    print('Features complete',features.terrain_quality,flush=True)
    result=evaluate(infer,features)
    result.update(date='2026-11-22',city_revision=reg.revision,terrain_quality=features.terrain_quality,
        obstacle_counts=features.obstacle_counts,feature_evidence=features.evidence,
        input_sha256={str(p):sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),Path('city_sim/facade_inference.py'),Path('city_sim/data/terrain.npz')]},
        elapsed_seconds=time.perf_counter()-start)
    (output/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps(result['metrics'],ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    run(p.parse_args().output)
