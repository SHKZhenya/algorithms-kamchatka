"""OSM topology-first building association and separate, explainable entrance hypotheses."""
from collections import defaultdict, Counter
import math
from shapely import STRtree
from shapely.geometry import Point, LineString, Polygon, shape
from shapely.ops import transform
from weather_coverage.geography import TO_METRIC,TO_WGS84,tags
from .access import osm_id,category
from .geodata import feature,polygons


def enrich_entrances(registry,city):
    catalog=registry.catalog;elements=city.osm_elements
    ids=catalog.building_ids;geoms=catalog.metric_buildings;tree=catalog.building_index
    membership=defaultdict(set)
    # Exact node membership supports multipolygon exterior and building parts.
    def refs(key,seen=None):
        seen=set() if seen is None else seen
        if key in seen or key not in elements:return set()
        seen.add(key);e=elements[key]
        if key.startswith('w'):return {'n'+n.get('ref') for n in e.findall('nd')}
        if key.startswith('r'):
            result=set()
            for m in e.findall('member'):
                if m.get('type') in {'way','relation'}:result|=refs(m.get('type')[0]+m.get('ref'),seen)
            return result
        return set()
    for bid in ids:
        if not bid.startswith('osm:'):continue
        key=bid.split(':')[1][0]+bid.split(':')[2]
        for node in refs(key):membership[node].add(bid)
    part_parent={}
    for key,e in elements.items():
        if not key.startswith('w') or not tags(e).get('building:part'):continue
        ns=refs(key);points=[(float(elements[n].get('lon')),float(elements[n].get('lat'))) for n in sorted(ns) if n in elements]
        if not points:continue
        p=transform(TO_METRIC,Point(points[0]))
        candidates=tree.query(p.buffer(2),predicate='intersects')
        # A part is linked only to a containing/overlapping building, never just closest arbitrary footprint.
        if len(candidates)==1:
            bid=ids[candidates[0]];part_parent[key]=bid
            for node in ns:membership[node].add(bid)
    improved=Counter()
    for key,e in elements.items():
        meta=tags(e)
        if not key.startswith('n') or not (meta.get('entrance') not in {None,'no'} or meta.get('building')=='entrance'):continue
        oid=osm_id(key);p=Point(float(e.get('lon')),float(e.get('lat')));metric=transform(TO_METRIC,p)
        exact=sorted(membership[key])
        near=tree.query(metric.buffer(8),predicate='intersects')
        # Prefer exact topology; otherwise a unique nearby boundary supported by an address or very small gap.
        options=sorted((geoms[i].boundary.distance(metric),ids[i]) for i in near)
        bid=exact[0] if len(exact)==1 else None
        method='shared_osm_outline_or_part_node' if bid else 'unresolved'
        if bid is None and not exact and options:
            close=[(d,b) for d,b in options if d<=1.5]
            addr_house=meta.get('addr:housenumber');addr_street=meta.get('addr:street')
            address_matches=[]
            for d,b in options:
                if not addr_house:continue
                address=registry.buildings[b]['properties'].get('address') or ''
                house = address.rsplit(',', 1)[-1].strip().casefold().replace(' ', '')
                if house == addr_house.casefold().replace(' ', '') and (not addr_street or addr_street.casefold() in address.casefold()):address_matches.append((d,b))
            choices=address_matches if len(address_matches)==1 else close
            if len(choices)==1:
                bid=choices[0][1];method='unique_address_and_boundary_8m' if address_matches else 'unique_boundary_within_1_5m'
        previous=registry.objects.get(oid)
        if previous:
            obj=previous
            # Existing ambiguous proximity assignments must remain transparent, exact topology can repair them.
            if bid and previous['properties'].get('building_id')!=bid:improved['relinked_real_entrances']+=1
            if not bid:bid=previous['properties'].get('building_id');method='legacy_proximity_3m' if bid else method
        else:
            improved['additional_real_entrances']+=1
            obj=feature(p,oid,object_id=oid,kind='entrance',role=meta.get('entrance'),category='other',
                        name=meta.get('name'),source='OpenStreetMap',connection_ids=catalog.membership.get(key,[]),location_verified=False)
            registry.objects[oid]=obj
        props=obj['properties'];props.update(building_id=bid,building_match_method=method,
            building_match_candidates=exact or [b for d,b in options],building_match_ambiguous=len(exact)>1,
            coordinate_source='OpenStreetMap',entrance_status='mapped',source_timestamp=e.get('timestamp'),source_osm_version=e.get('version'))
        if bid:
            bk=bid.split(':')[1][0]+bid.split(':')[2] if bid.startswith('osm:') else ''
            bmeta=tags(elements[bk]) if bk in elements else {'building':registry.buildings[bid]['properties'].get('building_type')}
            props['category']=category(bmeta)
    return dict(improved)


def entrance_candidates(registry):
    """At most one hypothesis per building, from real path ends near a facade.

    The connecting segment is evidence for a candidate only. It is never inserted
    into OSM topology or labelled a measured entrance. Solid crossings rejected.
    """
    catalog=registry.catalog
    endings=[];ending_roads=[];tiers=[];degree=Counter()
    for road in catalog.roads.features:
        props=road['properties']
        if props.get('foot') in {'no','private'} or props.get('access') in {'no','private'}:continue
        if props['mode']=='pedestrian':tier=0
        elif props['highway']=='service' and props.get('vehicle_candidate'):tier=1
        else:continue
        line=transform(TO_METRIC,shape(road['geometry']))
        for p in (Point(line.coords[0]),Point(line.coords[-1])):
            endings.append(p);ending_roads.append(road['id']);tiers.append(tier);degree[(round(p.x,3),round(p.y,3))]+=1
    keep=[i for i,p in enumerate(endings) if degree[(round(p.x,3),round(p.y,3))]==1]
    points=[endings[i] for i in keep];roads=[ending_roads[i] for i in keep];grades=[tiers[i] for i in keep];index=STRtree(points)
    known={f['properties'].get('building_id') for f in registry.objects.values() if f['properties']['kind']=='entrance'}
    candidates=[]
    for bid,geom in zip(catalog.building_ids,catalog.metric_buildings):
        if bid in known:continue
        choices=[]
        for i in index.query(geom.buffer(12),predicate='intersects'):
            p=points[i]
            if geom.contains(p):continue
            outer=min((poly.exterior for poly in polygons(geom)),key=lambda ring:ring.distance(p),default=None)
            if outer is None:continue
            wall=outer.interpolate(outer.project(p));gap=p.distance(wall)
            if not .2<=gap<=(12 if grades[i]==0 else 8):continue
            link=LineString([p,wall])
            blockers=catalog.building_index.query(link,predicate='intersects')
            if any(catalog.building_ids[j]!=bid and catalog.metric_buildings[j].intersection(link).length>.2 for j in blockers):continue
            choices.append((gap,int(i),wall,link))
        if not choices:continue
        gap,i,wall,link=min(choices,key=lambda x:(grades[x[1]],x[0],x[1]))
        method='nearest_pedestrian_endpoint_to_exterior_facade' if grades[i]==0 else 'service_driveway_endpoint_to_exterior_facade'
        candidates.append(feature(transform(TO_WGS84,wall),'candidate:'+bid+':path-end',kind='candidate_entrance',
            building_id=bid,entrance_status='hypothesis_not_observed',source='algorithm:osm_path_endpoint',
            method=method,
            reason='Real OSM access line endpoint is near facade without another building crossing; pedestrian evidence is preferred',
            confidence='moderate_geometric_evidence_unvalidated' if grades[i]==0 and gap<=3 else 'low_geometric_evidence_unvalidated',
            access_kind='pedestrian_hypothesis' if grades[i]==0 else 'driveway_hypothesis_pedestrian_access_unknown',
            gap_m=gap,evidence_road_id=roads[i],candidate_approach=mapping_safe(transform(TO_WGS84,link)),
            graph_connection_created=False,confirmed=False,confirmation_possible=True))
    return candidates


def mapping_safe(geom):
    from shapely.geometry import mapping
    return mapping(geom)
