"""Object relationship evidence from shared OSM nodes, separate from hypotheses."""
from collections import defaultdict
from shapely.geometry import box,shape


def relationships(registry,bounds):
    roads={f['id']:f for f in registry.catalog.roads.query(bounds)}
    links=[];seen=set()
    for node,ids in registry.catalog.membership.items():
        ids=sorted(set(ids)&roads.keys())
        for i,a in enumerate(ids):
            for b in ids[i+1:]:
                pa,pb=roads[a]['properties'],roads[b]['properties']
                if pa['mode']!=pb['mode']:kind='vehicle_to_pedestrian'
                elif pa.get('service') or pb.get('service') or pa['highway']=='service' or pb['highway']=='service':kind='road_to_access_way'
                else:kind='network_adjacency'
                if (a,b,node) in seen:continue
                seen.add((a,b,node));links.append({'from':a,'to':b,'kind':kind,'evidence':'shared_osm_node','node_id':'osm:node:'+node[1:]})
    extent=box(*bounds)
    for obj in registry.objects.values():
        if not extent.intersects(shape(obj['geometry'])):continue
        for rid in obj['properties'].get('connection_ids',[]):
            links.append({'from':rid,'to':obj['id'],'kind':'approach_to_entrance' if obj['properties']['kind']=='entrance' else 'road_to_object',
                          'evidence':'shared_osm_node','field_passability_verified':False})
    return {'revision':registry.revision,'links':links,'geometry_snapping_used':False,
            'candidate_links':[{'from':f['properties']['evidence_road_id'],'to':f['id'],'kind':'hypothesized_entrance_approach',
                               'evidence':f['properties']['method'],'graph_edge':False}
                               for f in registry.candidates if extent.intersects(shape(f['geometry']))]}
