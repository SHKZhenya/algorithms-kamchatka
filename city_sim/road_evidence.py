"""Separate attributed road observations, never automatic operational clearance.

Preserve source geometry/linear scope. Cartographic Overture widths are not field
measurements, and area/length is never converted to a local road width.
"""
import json
import math
import re
from pathlib import Path
from shapely.geometry import shape, mapping, box
from shapely.ops import transform,substring,unary_union,linemerge
from shapely.geometry import Point
import numpy as np
from weather_coverage.geography import TO_METRIC,TO_WGS84
from pyproj import Transformer


KINDS={'cartographic','official_inventory','design','estimated','field_measured'}


def linear_range(value):
    if value is None:return 0.,1.
    if not isinstance(value,(list,tuple)) or len(value)!=2:
        raise ValueError('Expected fractional linear range')
    a,b=map(float,value)
    if not math.isfinite(a+b) or not 0<=a<b<=1:
        raise ValueError('Invalid fractional range')
    return a,b


def overture_records(features,registry,city_polygon=None):
    roads={f['id']:f for f in registry.catalog.roads.features};records=[];audit={'rejected':[], 'unmatched_source_ways':[]}
    for f in features:
        p=f['properties']
        if p.get('subtype')!='road':continue
        geom=transform(TO_METRIC,shape(f['geometry']))
        if geom.geom_type!='LineString' or geom.length==0:continue
        sources=p.get('sources') or []
        for key,quantity in (('width_rules','width_m'),('road_surface','surface')):
            for nr,rule in enumerate(p.get(key) or []):
                try:
                    begin,end=linear_range(rule.get('between'))
                    value=rule['value']
                    if quantity=='width_m':
                        value=float(value)
                        if not math.isfinite(value) or not 0<value<=100:raise ValueError('Invalid width')
                except (KeyError,ValueError,TypeError) as error:
                    audit['rejected'].append({'source_id':f['id'],'field':key,'reason':str(error)});continue
                matching_sources=[s for s in sources if s.get('dataset')=='OpenStreetMap' and s.get('property','') in {'',key}]
                for src in matching_sources:
                    match=re.fullmatch(r'w(\d+)(?:@\d+)?',str(src.get('record_id','')))
                    if not match:continue
                    rid='osm:way:'+match[1]
                    if rid not in roads:
                        audit['unmatched_source_ways'].append(rid);continue
                    try:
                        sa,sb=linear_range(src.get('between'))
                    except ValueError:continue
                    lo,hi=max(begin,sa),min(end,sb)
                    if lo>=hi:continue
                    support=substring(geom,lo,hi,normalized=True)
                    if city_polygon is not None:support=support.intersection(city_polygon)
                    if support.is_empty:continue
                    target=transform(TO_METRIC,shape(roads[rid]['geometry']))
                    fraction=support.intersection(target.buffer(3,cap_style=2)).length/max(support.length,1e-9)
                    status='source_osm_id_and_geometry' if fraction>=.8 else 'source_osm_id_geometry_disagreement'
                    records.append(dict(type='Feature',id=f'overture:{f["id"]}:{key}:{nr}:{rid}',geometry=mapping(transform(TO_WGS84,support)),
                        properties=dict(osm_way_id=rid,source='Overture Maps Foundation / OpenStreetMap',
                            source_feature_id=f['id'],source_record_id=src.get('record_id'),source_update_time=src.get('update_time'),
                            release='2026-11-18.0',license='ODbL-1.0',kind='cartographic',quantity=quantity,value=value,
                            source_between=[lo,hi],match_status=status,geometry_agreement_fraction=fraction,
                            local_scope_verified=status=='source_osm_id_and_geometry',field_verified=False,
                            limitations=['Source map attribution, not independent measurement','Geometry agreement is not a road survey'])))
    audit['unmatched_source_ways']=sorted(set(audit['unmatched_source_ways']))
    return records,audit


def street_key(value):
    value=str(value or '').lower().replace('ё','е')
    value=re.sub(r'\b(улица|ул|проспект|просп|пр|шоссе|ш|проезд|пер|переулок|имени|им|бульвар|б-р)\b',' ',value)
    return re.sub(r'[^а-яa-z0-9]','',value)


def chainage(value):
    m=re.fullmatch(r'(\d+)\+(\d+(?:[.,]\d+)?)',str(value))
    if not m:raise ValueError('Unrecognized source chainage')
    return int(m[1])*1000+float(m[2].replace(',','.'))


def uniform_whole_part(rows,part,field):
    """Only one value covering the entire official interval is spatially located.

    Varying widths require chainage anchors; no normalized-length interpolation.
    """
    if not rows:return None
    try:
        begin=chainage(part.get('start','0+000'));end=chainage(part.get('finish')) if part.get('finish') else begin+float(part['length'])*1000
        values=[r.get(field) for r in rows]
        if any(v is None for v in values) or len({json.dumps(v,sort_keys=True,ensure_ascii=False) for v in values})!=1:return None
        intervals=sorted((chainage(r['start']),chainage(r['finish'])) for r in rows)
        cursor=begin
        for a,b in intervals:
            if b<a or a>cursor+1:return None
            cursor=max(cursor,b)
        if intervals[0][0]>begin+1 or cursor<end-1:return None
        return values[0]
    except (ValueError,TypeError,KeyError):return None


def anchor_interval(source,posts,start,finish):
    """Locate an interval only between nearby, monotonic official km-post anchors.

    No extrapolation, area/length conversion or proportional whole-road stretching.
    Position between posts is a derived location, not surveyed boundary accuracy.
    """
    line=source if source.geom_type=='LineString' else linemerge(source)
    if line.geom_type!='LineString':return None
    anchors=[]
    for post in posts:
        try:
            point=Point(TO_METRIC(float(post['longitude']),float(post['latitude'])))
            at=chainage(post['location'])
        except (KeyError,TypeError,ValueError):continue
        if point.distance(line)<=20:anchors.append((at,line.project(point)))
    anchors=sorted(set(anchors))
    if len(anchors)<3 or anchors[-1][0]-anchors[0][0]<2000:return None
    xs=np.array([a for a,b in anchors]);ys=np.array([b for a,b in anchors])
    if (np.diff(xs)<=0).any():return None
    if not ((np.diff(ys)>0).all() or (np.diff(ys)<0).all()):return None
    ratios=abs(np.diff(ys))/np.diff(xs)
    if ratios.min()<.7 or ratios.max()>1.3:return None
    try:a,b=chainage(start),chainage(finish)
    except ValueError:return None
    if not xs[0]<=a<b<=xs[-1]:return None
    low,high=np.interp([a,b],xs,ys)
    return substring(line,float(low),float(high)),dict(method='interpolation_between_official_km_posts',
        official_chainage_m=[a,b],anchors=len(anchors),anchor_projection_tolerance_m=20,
        geometric_length_per_chainage_range=[float(ratios.min()),float(ratios.max())],field_boundary_verified=False)


def skdf_records(objects,registry,city_polygon=None):
    roads=registry.catalog.roads.features;by_name={}
    for f in roads:
        # A carriageway inventory does not describe a parallel named sidewalk.
        if f['properties']['mode']=='vehicle':by_name.setdefault(street_key(f['properties'].get('name')),[]).append(f)
    to_metric=Transformer.from_crs(3857,32657,always_xy=True).transform
    records=[];audit=[]
    def divided_ambiguity(target):
        from weather_coverage.geography import tags
        rid=target['id'];meta=tags(registry.catalog_city_elements['w'+rid.split(':')[-1]])
        if meta.get('oneway') not in {'yes','1','true','-1'}:return False
        line=transform(TO_METRIC,shape(target['geometry']));cat=registry.catalog
        for j in cat.road_index.query(line.buffer(20),predicate='intersects'):
            other=cat.roads.features[j]
            if other['id']==rid or street_key(other['properties'].get('name'))!=street_key(target['properties'].get('name')):continue
            ometa=tags(registry.catalog_city_elements['w'+other['id'].split(':')[-1]])
            if ometa.get('oneway') not in {'yes','1','true','-1'}:continue
            g=cat.metric_roads[j]
            if line.interpolate(.5,normalized=True).distance(g)>.2 and line.intersection(g.buffer(20,cap_style=2)).length/max(line.length,1e-9)>.6:return True
        return False
    for obj in objects:
        card=obj.get('card',{}).get('data',{});root=obj['registry'];geomdata=obj.get('geometry',{})
        source=unary_union([transform(to_metric,shape(f['geometry'])) for f in geomdata.get('features',[]) if f.get('geometry')])
        candidates=by_name.get(street_key(root['full_name']),[])
        # Federal road route reference can identify a multi-street route, but
        # geometry must still agree. It is not used to locate varying widths.
        if root.get('road_number_full') and root['full_name'].startswith('А-401'):
            from weather_coverage.geography import tags
            candidates=[f for f in roads if f['properties']['mode']=='vehicle' and ('А-401' in tags(registry.catalog_city_elements['w'+f['id'].split(':')[-1]]).get('ref','') or 'A-401' in tags(registry.catalog_city_elements['w'+f['id'].split(':')[-1]]).get('ref',''))]
        associations=[]
        if not source.is_empty:
            for target in candidates:
                line=transform(TO_METRIC,shape(target['geometry']));support=line.intersection(source.buffer(8,cap_style=2))
                fraction=support.length/max(line.length,1e-9)
                if fraction>=.8:
                    if city_polygon is not None:support=support.intersection(city_polygon)
                    if not support.is_empty and support.length>0:associations.append((target,support,fraction))
        row=dict(road_id=root['id'],passport_id=root['passport_id'],name=root['full_name'],
            named_candidate_ways=[f['id'] for f in candidates],matched_osm_ways=[f['id'] for f,_,_ in associations],
            source_blocks=obj.get('card',{}).get('blocks',{}),width_rows=[],located_quantities=[],errors=obj.get('errors',[]),
            source_area_m2=card.get('square'),source_length_km=card.get('length'),area_divided_by_length_used=False)
        # Parent geometry is safe for an invariant value only if there is one
        # complete inventory part; mixed/partial parts stay explicit raw evidence.
        children=root.get('children',[])
        for blockname,quantity,field in (('roadway','width_m','roadway_width'),('lanes','lanes','lanes_name'),('pavement','surface','pavement_kind')):
            blocks=[b for k,b in obj.get('blocks',{}).items() if k.endswith(':'+blockname)]
            if blockname=='roadway':row['width_rows']=[r for b in blocks for r in b['records']]
            value=None
            if len(children)==1 and len(blocks)==1:
                value=uniform_whole_part(blocks[0]['records'],{**children[0],'start':card.get('start','0+000'),'finish':card.get('finish')},field)
            if value is None:continue
            if quantity=='surface' and isinstance(value,dict):value=value.get('name')
            if quantity=='width_m':
                try:value=float(value)
                except (ValueError,TypeError):continue
                if not math.isfinite(value) or not 0<value<=100:continue
            if quantity=='lanes' and (not isinstance(value,(int,float)) or value<=0):continue
            row['located_quantities'].append(quantity)
            for f,support,fraction in associations:
                ambiguous=quantity in {'width_m','lanes'} and divided_ambiguity(f)
                records.append(dict(type='Feature',id=f'skdf:{root["passport_id"]}:{quantity}:{f["id"]}',geometry=mapping(transform(TO_WGS84,support)),
                    properties=dict(osm_way_id=f['id'],source='ФГИС СКДФ: public portal passport',source_feature_id=str(root['id']),
                        source_passport_id=root['passport_id'],source_url=f'https://xn--d1aluo.xn--p1ai/passports/roads/{root["passport_id"]}',
                        retrieved_on='2026-11-22',license='public_access_reuse_terms_not_verified',kind='official_inventory',quantity=quantity,
                        value=value,match_status='same_road_name_and_geometry_8m',geometry_agreement_fraction=fraction,
                        local_scope_verified=not ambiguous,carriageway_assignment_ambiguous=ambiguous,field_verified=False,source_chainage_rows=blocks[0]['records'],
                        limitations=['Inventory statement is not a fresh field measurement','Only invariant whole-part attributes are transferred',
                                      'Local-only research; redistribution/use terms must be established before external publication'])))
        posts=[p for key,b in obj.get('blocks',{}).items() if key.endswith(':km-posts') for p in b['records']]
        if 'width_m' not in row['located_quantities'] and posts:
            for nr,widthrow in enumerate(row['width_rows']):
                located=anchor_interval(source,posts,widthrow.get('start'),widthrow.get('finish'))
                try:value=float(widthrow['roadway_width'])
                except (ValueError,TypeError,KeyError):continue
                if located is None or not math.isfinite(value) or not 0<value<=100:continue
                piece,anchor_quality=located
                if city_polygon is not None:piece=piece.intersection(city_polygon)
                if piece.is_empty:continue
                for target in candidates:
                    line=transform(TO_METRIC,shape(target['geometry']))
                    support=line.intersection(piece.buffer(8,cap_style=2))
                    if support.is_empty or support.length<1:continue
                    # Reject merely transverse crossings at an intersection.
                    if support.length/line.length<.8:continue
                    ambiguous=divided_ambiguity(target)
                    row['located_quantities'].append('width_m_anchored')
                    records.append(dict(type='Feature',id=f'skdf:{root["passport_id"]}:anchored:{nr}:{target["id"]}',geometry=mapping(transform(TO_WGS84,support)),
                        properties=dict(osm_way_id=target['id'],source='ФГИС СКДФ: public portal passport and km-posts',
                            source_feature_id=str(root['id']),source_passport_id=root['passport_id'],source_chainage_rows=[widthrow],
                            source_url=f'https://xn--d1aluo.xn--p1ai/passports/roads/{root["passport_id"]}',retrieved_on='2026-11-22',
                            license='public_access_reuse_terms_not_verified',kind='official_inventory',quantity='width_m',value=value,
                            match_status='route_reference_geometry_and_official_chainage_anchors',local_scope_verified=not ambiguous,
                            carriageway_assignment_ambiguous=ambiguous,
                            field_verified=False,anchor_quality=anchor_quality,
                            limitations=['Width is official inventory; interval position between km-posts is derived',
                                'Up to 8m matching tolerance near interval boundaries; do not infer street-level measurement',
                                'Local-only research; redistribution/use terms not verified'])))
        audit.append(row)
    return records,audit


def skdf_contexts(objects,audit,registry):
    """Retain unlocated sidewalks/obstacles as road-related records, not fake links."""
    associated={r['passport_id']:r for r in audit}
    road_by_id={f['id']:f for f in registry.catalog.roads.features};contexts=[]
    for obj in objects:
        root=obj['registry'];match=associated.get(root['passport_id'],{})
        ids=match.get('matched_osm_ways',[]);candidates=match.get('named_candidate_ways',[])
        if ids:
            bounds=unary_union([shape(road_by_id[rid]['geometry']) for rid in ids]).bounds
        else:
            to_wgs=Transformer.from_crs(3857,4326,always_xy=True).transform
            geoms=[transform(to_wgs,shape(f['geometry'])) for f in obj.get('geometry',{}).get('features',[]) if f.get('geometry')]
            if not geoms:continue
            bounds=unary_union(geoms).bounds
        for key,block in obj.get('blocks',{}).items():
            kind=key.split(':')[-1]
            if kind not in {'sidewalks','barrier-fences','clearances','gabarit','median-separations'}:continue
            for row in block['records']:
                quantities=[]
                if kind=='sidewalks' and row.get('sidewalk_width') is not None:quantities.append(('sidewalk_width_m',row['sidewalk_width']))
                if kind=='barrier-fences':quantities.append(('barrier_fence_inventory',True))
                if kind=='clearances':
                    if row.get('width') is not None:quantities.append(('clearance_width_m',row['width']))
                    if row.get('is_height_limited') and row.get('height') is not None:quantities.append(('clearance_height_m',row['height']))
                if kind=='gabarit':
                    if row.get('weight_limit') is not None:quantities.append(('reported_weight_limit_t',row['weight_limit']))
                if kind=='median-separations':quantities.append(('median_inventory_description',row.get('separation')))
                for quantity,value in quantities:
                    contexts.append(dict(id=f'skdf-context:{root["passport_id"]}:{row["id"]}:{quantity}',
                        source='ФГИС СКДФ public portal',source_passport_id=root['passport_id'],source_block=kind,source_record=row,
                        license='public_access_reuse_terms_not_verified',kind='official_inventory',quantity=quantity,value=value,
                        osm_way_ids=ids,candidate_osm_way_ids=candidates,support_bbox=list(bounds),local_scope_verified=False,field_verified=False,
                        road_association='name_and_geometry' if ids else 'unverified_named_candidates_or_unmatched',
                        current_validity='not_verified; gabarit may refer to temporary 2024 orders' if kind=='gabarit' else 'unknown_observation_date',
                        limitations=['Related road identity established; exact local obstacle/sidewalk support unresolved',
                            'Do not assign a carriageway attribute to a pedestrian way or use an old order as a current restriction']))
    return contexts


class RoadEvidence:
    def __init__(self,path,revision=None):
        self.path=Path(path);self.data=json.loads(self.path.read_text())
        if not isinstance(self.data,dict) or self.data.get('type')!='FeatureCollection' or not isinstance(self.data.get('features'),list):
            raise ValueError('Expected road evidence FeatureCollection')
        meta=self.data.get('metadata',{})
        if not isinstance(meta,dict):raise ValueError('Expected evidence metadata')
        if revision is not None and meta.get('city_revision')!=revision:
            raise RuntimeError('Road evidence and OSM revision differ')
        if not isinstance(self.data.get('contexts',[]),list):raise ValueError('Expected context records')
        for context in self.data.get('contexts',[]):
            if not isinstance(context,dict) or context.get('kind') not in KINDS or not context.get('source') or not context.get('license'):
                raise ValueError('Context provenance is missing')
            bounds=context.get('support_bbox')
            if not isinstance(bounds,(list,tuple)) or len(bounds)!=4:raise ValueError('Expected context WGS84 bbox')
            w,s,e,n=map(float,bounds)
            if not -180<=w<=e<=180 or not -90<=s<=n<=90:raise ValueError('Invalid context bbox')
        for f in self.data.get('features',[]):
            p=f['properties'];g=shape(f['geometry'])
            if not isinstance(p,dict):raise ValueError('Expected properties')
            if p.get('kind') not in KINDS or not p.get('source') or not p.get('license'):
                raise ValueError('Missing provenance or unknown evidence kind')
            if not g.is_valid or g.is_empty or g.geom_type not in {'LineString','MultiLineString'}:
                raise ValueError('Road evidence needs valid line support')
            w,s,e,n=g.bounds
            if not -180<=w<=e<=180 or not -90<=s<=n<=90:raise ValueError('Expected WGS84 evidence geometry')
            if p.get('quantity')=='width_m':
                try:valid=math.isfinite(float(p['value'])) and 0<float(p['value'])<=100
                except (TypeError,KeyError,ValueError):valid=False
                if not valid:raise ValueError('Invalid road width')
        self.metadata=meta

    def query(self,bounds,way_id=None):
        extent=box(*bounds)
        return dict(type='FeatureCollection',features=[f for f in self.data['features']
            if (way_id is None or f['properties'].get('osm_way_id')==way_id) and extent.intersects(shape(f['geometry']))],
            contexts=[c for c in self.data.get('contexts',[]) if (way_id is None or way_id in c.get('osm_way_ids',[]) or way_id in c.get('candidate_osm_way_ids',[]))
                      and c.get('support_bbox') and extent.intersects(box(*c['support_bbox']))],
            metadata={**self.metadata,'operational_access_inferred':False,'canonical_graph_modified':False})


def coverage(records,registry):
    baseline={f['id']:f for f in registry.catalog.roads.features}
    by_quantity={}
    for quantity,tag in (('width_m','width'),('surface','surface'),('lanes','lanes')):
        eligible=[f for f in records if f['properties'].get('quantity')==quantity and f['properties'].get('local_scope_verified')]
        ids={f['properties']['osm_way_id'] for f in eligible}
        from .equipment_access import metres
        known={rid for rid,f in baseline.items() if (metres(f['properties'].get('width')) is not None if quantity=='width_m'
            else bool(f['properties'].get(tag)))}
        # Catalog does not retain lanes: use original OSM tags for baseline.
        if quantity=='lanes':
            from weather_coverage.geography import tags
            known={rid for rid in baseline if tags(registry.catalog_city_elements['w'+rid.split(':')[-1]]).get('lanes')}
        total=0.;new_length=0.
        for rid in sorted(ids):
            support=unary_union([transform(TO_METRIC,shape(f['geometry'])) for f in eligible if f['properties']['osm_way_id']==rid])
            road=transform(TO_METRIC,shape(baseline[rid]['geometry']))
            length=road.intersection(support.buffer(3,cap_style=2)).length
            total+=length
            if rid not in known:new_length+=length
        by_quantity[quantity]=dict(source_records=len(eligible),matched_osm_ways=len(ids),new_osm_ways=len(ids-known),
            baseline_osm_ways=len(known),union_osm_ways=len(ids|known),covered_length_m=total,new_covered_length_m=new_length,
            field_measured_ways=len({f['properties']['osm_way_id'] for f in eligible if f['properties']['kind']=='field_measured'}))
    return by_quantity
