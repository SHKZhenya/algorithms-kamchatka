"""Research facade ranking; labels are mapped doors, never a census of absent doors.

Geometry features do not use door coordinates. Spatial CV rebuilds every learned
component after withholding complete 500 m blocks plus a 100 m guard zone.
"""
import math
from hashlib import sha256
import numpy as np
from shapely import STRtree
from shapely.geometry import LineString, Point, box, shape, mapping
from shapely.ops import transform, unary_union
from weather_coverage.geography import TO_METRIC, TO_WGS84
from .geodata import polygons, feature, collection
from .object_registry import facade

VERSION = 'facade-rank-1'
FEATURES = ['log_length', 'relative_length', 'foot_distance', 'service_distance',
            'street_distance', 'outward_open_distance', 'log_area', 'elongation']


def fold_of(block):
    return int(sha256(f'{block[0]},{block[1]}'.encode()).hexdigest()[:8], 16) % 5


class FacadeInference:
    def __init__(self, registry):
        self.registry = registry
        cat = registry.catalog
        road_groups = [[g for f, g in zip(cat.roads.features, cat.metric_roads)
                        if predicate(f['properties'])] for predicate in (
            lambda p: p['mode'] == 'pedestrian' and p.get('foot') not in {'no','private'} and p.get('access') not in {'no','private'},
            lambda p: p['highway'] == 'service',
            lambda p: p['mode'] == 'vehicle' and p['highway'] != 'service')]
        trees = [STRtree(g) for g in road_groups]
        self.rows = {}
        self.labels = {}
        self.centers = {}
        self.blocks = {}
        for bid, geom in zip(cat.building_ids, cat.metric_buildings):
            # Simplify survey jitter/collinear nodes; retain courtyards as candidates.
            walls = []
            for poly in polygons(geom.simplify(.5, preserve_topology=True)):
                for ring in (poly.exterior, *poly.interiors):
                    coords = list(ring.coords)
                    walls.extend(LineString([a,b]) for a,b in zip(coords,coords[1:]) if Point(a).distance(Point(b)) >= 2)
            if not walls:
                continue
            center = geom.centroid
            self.centers[bid] = (center.x, center.y)
            self.blocks[bid] = (math.floor(center.x/500), math.floor(center.y/500))
            maxlen = max(w.length for w in walls)
            rect = list(geom.minimum_rotated_rectangle.exterior.coords)
            sides = [Point(a).distance(Point(b)) for a,b in zip(rect,rect[1:])]
            elong = min(10., max(sides)/max(.1,min(sides)))
            rows = []
            for i, wall in enumerate(walls):
                mid = wall.interpolate(.5, normalized=True)
                normal = facade(geom, mid)
                if normal is None:
                    continue
                _, anchor, nx, ny = normal
                ray = LineString([(anchor.x+nx,anchor.y+ny),(anchor.x+30*nx,anchor.y+30*ny)])
                other = [cat.metric_buildings[j] for j in cat.building_index.query(ray, predicate='intersects')
                         if cat.building_ids[j] != bid]
                hits = ray.intersection(unary_union(other)) if other else None
                open_m = anchor.distance(hits) if hits is not None and not hits.is_empty else 30.
                distances = [min(100., wall.distance(group[t.nearest(wall)])) if group else 100.
                             for group,t in zip(road_groups,trees)]
                x = [math.log1p(wall.length)/5,wall.length/maxlen,
                     *[d/100 for d in distances],open_m/30,math.log1p(geom.area)/10,elong/10]
                area = wall.buffer(6, cap_style=2).difference(geom)
                blockers = cat.building_index.query(area, predicate='intersects')
                if len(blockers):
                    area = area.difference(unary_union([cat.metric_buildings[j] for j in blockers]))
                rows.append(dict(id=f'{bid}:facade:{i}',wall=wall,area=area,x=x,
                                 normal=(nx,ny),distances_m=distances))
            self.rows[bid] = rows
            labels = set()
            for oid in registry.by_building.get(bid, []):
                obj = registry.objects[oid]
                # Avoid known ambiguous building associations and reported reviews in benchmark.
                p = obj['properties']
                if p.get('building_match_ambiguous') or p.get('building_link_ambiguous') or p.get('source') != 'OpenStreetMap':
                    continue
                point = transform(TO_METRIC,shape(obj['geometry']))
                distances = sorted((r['wall'].distance(point),j) for j,r in enumerate(rows))
                if distances and distances[0][0] <= 3:
                    # A corner does not identify which facade contains the door.
                    if len(distances)>1 and distances[1][0]-distances[0][0]<.25:
                        continue
                    labels.add(distances[0][1])
            if labels:
                self.labels[bid] = labels

    def fit(self, train_ids):
        ids = sorted(set(train_ids) & self.labels.keys())
        xs, ys, weights = [], [], []
        for bid in ids:
            for j,row in enumerate(self.rows[bid]):
                xs.append([1.,*row['x']]); ys.append(float(j in self.labels[bid]))
                weights.append(1/len(self.rows[bid]))
        if len(ids)<10:
            return np.zeros(9), ids
        x,y,w = np.array(xs),np.array(ys),np.array(weights)
        w /= w.sum()
        beta = np.zeros(x.shape[1])
        # Fixed regularized logistic objective; unmapped walls are noisy negatives.
        for _ in range(400):
            pred = 1/(1+np.exp(-np.clip(x@beta,-30,30)))
            penalty = .01*beta; penalty[0] = 0
            beta -= 1.5*(x.T@(w*(pred-y))+penalty)
        return beta, ids

    def scores(self, bid, model):
        beta, train = model
        rows = self.rows[bid]
        base = np.array([[1.,*r['x']] for r in rows])@beta
        # Similar local buildings vote on orientation; no building-series claim.
        c = np.array(self.centers[bid])
        neighbours = []
        typ = self.registry.buildings[bid]['properties'].get('building_type')
        for other in train:
            if other == bid or self.registry.buildings[other]['properties'].get('building_type') != typ:
                continue
            d = np.linalg.norm(np.array(self.centers[other])-c)
            if d > 1000:
                continue
            shape_delta = abs(rows[0]['x'][6]-self.rows[other][0]['x'][6])+abs(rows[0]['x'][7]-self.rows[other][0]['x'][7])
            neighbours.append((d,other, math.exp(-d/300-4*shape_delta)))
        neighbours = sorted(neighbours)[:12]
        if neighbours:
            vote = np.zeros(len(rows)); total = 0.
            for _,other,weight in neighbours:
                normals = [self.rows[other][j]['normal'] for j in self.labels[other]]
                vote += weight*np.array([max(max(0.,np.dot(r['normal'],n))**4 for n in normals) for r in rows])
                total += weight
            base += .5*vote/max(total,1e-12)
        return base, [o for _,o,_ in neighbours]

    def validate(self):
        output = []; folds = []
        labelled = set(self.labels)
        for fold in range(5):
            test = sorted(b for b in labelled if fold_of(self.blocks[b]) == fold)
            blocks = sorted({self.blocks[b] for b in test})
            guard = STRtree([box(x*500-100,y*500-100,(x+1)*500+100,(y+1)*500+100) for x,y in blocks])
            train = sorted(b for b in labelled if fold_of(self.blocks[b]) != fold
                           and not len(guard.query(Point(*self.centers[b]),predicate='intersects')))
            model = self.fit(train)
            folds.append(dict(fold=fold,train_buildings=len(train),test_buildings=len(test),test_blocks=len(blocks),
                              training_building_ids=train,test_building_ids=test,coefficients=model[0].tolist()))
            for bid in test:
                rows = self.rows[bid]; scores, neighbours = self.scores(bid,model)
                methods = {'model':scores,'nearest_footway':[-r['distances_m'][0] for r in rows],
                           'longest_facade':[r['wall'].length for r in rows]}
                record = dict(building_id=bid,fold=fold,observed_facades=sorted(self.labels[bid]),
                              candidate_facades=len(rows),neighbour_ids=neighbours,methods={})
                for name, values in methods.items():
                    rank = sorted(range(len(rows)),key=lambda i:(-values[i],rows[i]['id']))
                    record['methods'][name] = dict(top1_hit=rank[0] in self.labels[bid],
                        top2_recall=len(set(rank[:2])&self.labels[bid])/len(self.labels[bid]),
                        top2_wall_fraction=sum(rows[i]['wall'].length for i in rank[:2])/sum(r['wall'].length for r in rows))
                output.append(record)
        metrics = {name:{key:float(np.mean([r['methods'][name][key] for r in output])) if output else None
                         for key in ('top1_hit','top2_recall','top2_wall_fraction')}
                   for name in ('model','nearest_footway','longest_facade')}
        return dict(model=VERSION,block_size_m=500,guard_m=100,folds=folds,metrics=metrics,predictions=output,
                    labelled_buildings=len(labelled),limitations=['Observed facade recovery only; unmapped doors are not negatives in reality',
                    'OSM mapping bias; no field test, building series or count ground truth',
                    'Footway geometry can already encode a mapped entrance approach'])

    def predict(self, bid, state=None):
        if bid not in self.registry.buildings:
            raise LookupError('Building not found')
        if bid not in self.rows:
            return collection([],building_id=bid,entrance_status='unknown',reason='no_supported_facade')
        model = self.fit(set(self.labels)-{bid})
        scores, neighbours = self.scores(bid,model)
        rank = sorted(range(len(scores)),key=lambda i:(-scores[i],self.rows[bid][i]['id']))
        rows = []
        sampler = None
        if state is not None:
            from .spatial import RasterAssessment
            sampler = RasterAssessment(np.where(state.area>0,state.mass,np.nan),state.bounds,metric_coordinates=True)
        for position,j in enumerate(rank):
            r = self.rows[bid][j]
            physical = sampler.assess(r['area'].intersection(state.ground) if hasattr(state,'ground') else r['area'],0,False) if sampler else None
            if physical:
                physical = {'mean_kg_m2':physical['mean_cm'],'p90_kg_m2':physical['p90_cm'],
                            'coverage_fraction':physical['coverage_fraction'],'model': 'snow-mass-fv-1'}
            rows.append(feature(transform(TO_WGS84,r['wall']),r['id'],rank=position+1,
                ranking_score=float(scores[j]),probability=None,entrance_status='hypothesis_not_observed',
                source=VERSION,confidence='unvalidated_model_ranking',assessment_area=mapping(transform(TO_WGS84,r['area'])),
                physical=physical,features=dict(zip(FEATURES,r['x'])),observed_entrance_ids=[oid for oid in self.registry.by_building.get(bid,[])
                    if transform(TO_METRIC,shape(self.registry.objects[oid]['geometry'])).distance(r['wall'])<=3]))
        return collection(rows,model=VERSION,revision=self.registry.revision,building_id=bid,
            probable_facade_ids=[self.rows[bid][j]['id'] for j in rank[:2]],
            neighbour_building_ids=neighbours,training_buildings=len(model[1]),predicted_entrance_count=None,
            exact_door_coordinates_inferred=False,graph_connections_created=False,
            limitations=['Ranking scores are not calibrated probabilities','Evaluate multiple facades; entrance count remains unknown'])
