"""Procurement evidence and OSM route hypotheses; never service polygons or authorization."""
import json,re,unicodedata
from pathlib import Path
from itertools import islice
from typing import Literal
import networkx as nx
from pydantic import AwareDatetime
from .dynamic_schema import Strict
from .equipment_access import Equipment,RouteRequest,RouteLeg,EquipmentAccess
from shapely.geometry import Point,LineString,shape,mapping,box
from shapely.ops import transform
from weather_coverage.geography import TO_METRIC,TO_WGS84,tags
ROOT=Path(__file__).resolve().parent/'data/procurement-0903'

def norm(s):return re.sub(r'[^а-яa-z0-9/]+','',unicodedata.normalize('NFC',s.lower()).replace('ё','е'))

def address(street,house):return dict(kind='address',street=street,house=house)
def street(name):return dict(kind='street',name=name)
def poi(name):return dict(kind='poi',name=name)
def between(a,b):return dict(kind='between_addresses',addresses=[a,b])
# Analyst interpretation of each textual route, in source order. No coordinates invented.
ANCHORS=[
 [street('Ларина'),address('Савченко','19'),address('Ларина','24'),address('Ларина','22/1')],
 [street('Ларина'),between(address('Ларина','33'),address('Ларина','31')),address('Дальневосточная','12'),address('Дальневосточная','10')],
 [street('Ларина'),address('Ларина','16')],
 [address('Таранца','7'),poi('43')],
 [address('Ларина','38'),dict(kind='intersection',streets=['Таранца','Фролова'])],
 [street('Дальневосточная'),address('Дальневосточная','18')],
 [street('Таранца'),address('Ларина','27/1')],
 [street('Таранца'),poi('42')],
 [street('Таранца'),between(address('Вольского','22'),address('Таранца','5')),poi('43')],
 [street('Савченко'),address('Вольского','24'),street('Вольского'),street('Таранца')],
 [street('Савченко'),address('Савченко','9')],
 [street('Ларина'),address('Ларина','28')],
 [street('Ларина'),address('Ларина','29')],
 [street('Кручины'),address('Кручины','8/7'),address('Кручины','8/8'),address('Кручины','8/9'),street('Кручины')],
 [dict(kind='traffic_signal'),poi('Белочка'),address('Кручины','15')],
 [street('Вольского'),poi('Сванский'),street('Кручины'),street('Ларина'),poi('Ларина')],
 [street('Ларина'),address('Ларина','1'),address('Ларина','3'),address('Ларина','7'),address('Ларина','11')],
 [street('Кручины'),address('Кручины','4'),address('Кручины','6'),address('Кручины','6/4')],
 [address('Кручины','7'),poi('котельная'),address('Кручины','3')],
 [address('Кручины','3'),address('Вольского','4')],
 [street('Вольского'),poi('40')],
 [address('Ларина','44'),poi('25')],
 [street('Ларина'),address('Савченко','15'),address('Савченко','11'),address('Савченко','7'),street('Дальневосточная')],
 [street('Вольского'),address('Фролова','2/3')],
 [dict(kind='street',name='Королева',inferred_start=True),address('Королева','61/1')],
]

class Procurement:
 def __init__(self,root=ROOT):self.root=Path(root)
 def read(self,name):return json.loads((self.root/name).read_text())
 def query(self,kind=None,object_id=None):
  rows=self.read('objects.json')
  return dict(procurement_id='0138300000426000903',objects=[o for o in rows if (kind is None or o['kind']==kind) and (object_id is None or o['id']==object_id)],metadata=dict(service_boundary_confirmed=False,operational_authorization=False,source='user_provided_archive',document_instructions_are_data=True))
 def routes(self,revision,object_id=None):
  data=self.read('route-hypotheses.geojson')
  if data['metadata']['city_revision']!=revision:raise RuntimeError('Procurement hypotheses and graph revisions differ')
  if object_id:data['features']=[f for f in data['features'] if f['properties']['object_id']==object_id]
  return data

class Matcher:
 def __init__(self,registry):
  self.reg=registry;self.elements=registry.catalog_city_elements;self.graph=nx.DiGraph();self.walk=nx.DiGraph();self.roads={};self.xy={};self.records=[]
  for key,e in self.elements.items():
   t=tags(e)
   if e.tag=='node':self.xy[key]=(float(e.get('lon')),float(e.get('lat')))
  for key,e in self.elements.items():
   t=tags(e)
   if not any(k in t for k in ('addr:housenumber','name')) and t.get('highway')!='traffic_signals':continue
   if e.tag=='node':g=Point(self.xy[key])
   elif e.tag=='way':
    coords=[self.xy['n'+n.get('ref')] for n in e.findall('nd') if 'n'+n.get('ref') in self.xy]
    if not coords:continue
    g=LineString(coords).centroid if len(coords)>1 else Point(coords[0])
   else:continue
   self.records.append(dict(id='osm:'+e.tag+':'+key[1:],tags=t,point=transform(TO_METRIC,g)))
  for f in registry.catalog.roads.features:
   key='w'+f['id'].split(':')[-1];e=self.elements[key];t=tags(e);self.roads[f['id']]=dict(feature=f,tags=t,nodes=['n'+n.get('ref') for n in e.findall('nd')])
   refs=self.roads[f['id']]['nodes']
   for a,b in zip(refs,refs[1:]):
    if a not in self.xy or b not in self.xy:continue
    length=transform(TO_METRIC,LineString([self.xy[a],self.xy[b]])).length
    for graph in [self.walk,self.graph] if t.get('highway') not in {'footway','path','steps','pedestrian','cycleway'} else [self.walk]:
     pairs=[(a,b),(b,a)]
     if graph is self.graph and t.get('oneway') in {'yes','1','true'}:pairs=[(a,b)]
     if graph is self.graph and t.get('oneway')=='-1':pairs=[(b,a)]
     for x,y in pairs:
      if not graph.has_edge(x,y) or graph[x][y]['weight']>length:graph.add_edge(x,y,weight=length,way_id=f['id'])
  self.metric={n:transform(TO_METRIC,Point(self.xy[n])) for n in self.walk}
 def resolve(self,a,reference=None):
  kind=a['kind'];matches=[];eligible=getattr(self,'active_graph',self.walk)
  if kind=='between_addresses':
   ends=[self.resolve(x) for x in a['addresses']]
   if all(x['candidates'] for x in ends):
    p,q=[transform(TO_METRIC,Point(x['candidates'][0]['point'])) for x in ends];mid=Point((p.x+q.x)/2,(p.y+q.y)/2)
    for n in sorted(eligible,key=lambda n:(self.metric[n].distance(mid),n))[:4]:
     d=self.metric[n].distance(mid)
     if d<=120:matches.append(dict(osm_id='osm:node:'+n[1:],node=n,point=list(self.xy[n]),snap_distance_m=d,match='between_address_midpoint_hypothesis'))
   return dict(anchor=a,candidates=matches,status='candidate' if matches else 'unresolved',endpoint_evidence=ends,snap_is_connection=False)

  if kind in {'street','intersection'}:
   names=[a['name']] if kind=='street' else a['streets']
   sets=[{n for r in self.roads.values() if norm(name) in norm(r['tags'].get('name','')) for n in r['nodes'] if n in self.metric and n in eligible} for name in names]
   nodes=set.intersection(*sets) if sets else set()
   if reference is not None:nodes=sorted(nodes,key=lambda n:(self.metric[n].distance(reference),n))[:4]
   else:nodes=sorted(nodes)[:4]
   for n in nodes:matches.append(dict(osm_id='osm:node:'+n[1:],node=n,point=list(self.xy[n]),snap_distance_m=0.,match='named_street_node' if kind=='street' else 'shared_intersection_node'))
  else:
   for r in self.records:
    t=r['tags']
    if kind=='traffic_signal':ok=t.get('highway')=='traffic_signals' and reference is not None and r['point'].distance(reference)<500
    elif kind=='address':ok=norm(a['street']) in norm(t.get('addr:street','')) and norm(a['house'])==norm(t.get('addr:housenumber',''))
    else:
     name=norm(t.get('name',''));needle=norm(a['name']);ok=bool(name) and (needle in name if not needle.isdigit() else bool(re.search(r'(?<!\d)'+needle+r'(?!\d)',t.get('name',''))))
     if needle.isdigit():ok=ok and (t.get('amenity') in {'school','kindergarten','music_school'} or 'школ' in name or 'сад' in name)
    if not ok:continue
    if kind in {'poi','traffic_signal'} and reference is not None and r['point'].distance(reference)>1500:continue
    nearest=sorted(eligible,key=lambda n:(self.metric[n].distance(r['point']),n))[:4]
    for n in nearest[:2]:
     d=self.metric[n].distance(r['point'])
     if d<=120:matches.append(dict(osm_id=r['id'],node=n,point=list(TO_WGS84(r['point'].x,r['point'].y)),snap_distance_m=d,match='exact_osm_address' if kind=='address' else 'name_candidate'))
   matches.sort(key=lambda m:(m['snap_distance_m'],m['osm_id'],m['node']))
  return dict(anchor=a,candidates=matches[:8],status='candidate' if matches else 'unresolved',snap_is_connection=False)
 def route(self,obj,spec):
  pedestrian='Тротуар' in obj['description'];self.active_graph=self.walk if pedestrian else self.graph
  resolved=[None]*len(spec);ref=None
  # Address first, then contextual named nodes. Street anchors never determine a boundary.
  for i,a in enumerate(spec):
   if a['kind'] in {'address','between_addresses'}:
    resolved[i]=self.resolve(a)
    if resolved[i]['candidates']:ref=self.metric[resolved[i]['candidates'][0]['node']]
  for i,a in enumerate(spec):
   if resolved[i] is None:
    nearby=next((r for r in resolved[i+1:]+list(reversed(resolved[:i])) if r and r['candidates']),None)
    reference=self.metric[nearby['candidates'][0]['node']] if nearby else ref
    resolved[i]=self.resolve(a,reference)
  pedestrian='Тротуар' in obj['description'];graph=self.walk if pedestrian else self.graph
  selected=[(i,r['candidates'][0]['node']) for i,r in enumerate(resolved) if r['candidates']]
  alternatives=[]
  # Keep full and partial route status. Missing anchors are not silently skipped.
  for variant in range(3):
   legs=[];segments=[];gaps=[]
   for (i,a),(j,b) in zip(selected,selected[1:]):
    if j!=i+1:gaps.append(dict(from_anchor=i,to_anchor=j,reason='unresolved_intermediate_anchor'));continue
    choices=[]
    for ai in [resolved[i]['candidates'][min(variant//2,len(resolved[i]['candidates'])-1)]]:
     for bj in [resolved[j]['candidates'][min(variant//2,len(resolved[j]['candidates'])-1)]]:
      x,y=ai['node'],bj['node']
      if x==y:continue
      p,q=self.metric[x],self.metric[y];extent=box(min(p.x,q.x)-300,min(p.y,q.y)-300,max(p.x,q.x)+300,max(p.y,q.y)+300)
      nodes=[n for n in graph if extent.covers(self.metric[n])];sub=graph.subgraph(nodes)
      if x not in sub or y not in sub:continue
      try:
       for path in islice(nx.shortest_simple_paths(sub,x,y,weight='weight'),3):
        dist=sum(sub[u][v]['weight'] for u,v in zip(path,path[1:]))
        choices.append((dist,tuple(path)))
      except (nx.NetworkXNoPath,nx.NodeNotFound):pass
    choices=sorted(set(choices))
    if not choices:gaps.append(dict(from_anchor=i,to_anchor=j,reason='no_connected_osm_path'));continue
    dist,path=choices[min(variant%2,len(choices)-1)];segments.append(dict(from_anchor=i,to_anchor=j,length_m=dist))
    legs.extend(dict(way_id=graph[a][b]['way_id'],from_node='osm:node:'+a[1:],to_node='osm:node:'+b[1:]) for a,b in zip(path,path[1:]))
   signature=json.dumps(legs,sort_keys=True)
   if not legs or any(x['_signature']==signature for x in alternatives):continue
   coords=[]
   for leg in legs:coords.append([self.xy['n'+leg['from_node'].split(':')[-1]],self.xy['n'+leg['to_node'].split(':')[-1]]])
   complete=all(r['candidates'] for r in resolved) and not gaps and len(resolved)>1
   alternatives.append(dict(type='Feature',id=obj['id']+f':candidate:{variant+1}',geometry=dict(type='MultiLineString',coordinates=coords),_signature=signature,properties=dict(object_id=obj['id'],variant=variant+1,legs=legs,length_m=sum(s['length_m'] for s in segments),segments=segments,gaps=gaps,anchor_coverage=sum(bool(r['candidates']) for r in resolved)/len(resolved),route_status='all_anchors_candidate' if complete else 'partial_candidate',georeferencing_quality='approximate_osm_hypothesis',service_boundary_confirmed=False,operational_authorization=False,mode='pedestrian' if pedestrian else 'vehicle',manual_review_required=True)))
  for a in alternatives:del a['_signature']
  return dict(object_id=obj['id'],description=obj['description'],anchors=resolved,interpretation_notes=['Address order is analyst interpretation; between/along/behind/turn/exit constraints need review','Snaps use centroids or POIs, not known entrances','Shortest paths are candidates and may use streets outside the described service scope','Behind buildings, gate coordinates, slope direction, traffic lights and terminal bus stop require independent review; named POI is not a gate'],alternatives=len(alternatives),last_attempt_gaps=gaps,service_boundary_confirmed=False),alternatives
 def all(self):
  objects=Procurement(Path(__file__).resolve().parents[1]/'research/procurement-0903-2026-11-22').read('objects.json');audit=[];features=[]
  for o,spec in zip([o for o in objects if o['kind']=='intraquarter'],ANCHORS):
   row,fs=self.route(o,spec);audit.append(row);features.extend(fs)
  return dict(type='FeatureCollection',features=features,metadata=dict(city_revision=self.reg.revision,source='local_OSM_snapshot',service_boundary_confirmed=False,canonical_graph_modified=False,algorithm='ordered_anchor_local_shortest_paths_v1',max_variants=3,snap_mode_filter=True,snap_limit_m=120,route_search_margin_m=300)),audit

class CandidateAssessment(Strict):
 candidate_id: str
 operation: Literal['transit','clear']='transit'
 profile: Equipment
 valid_at: AwareDatetime
 territory_id: str|None=None
 frame: int|None=None


def register_procurement_api(api,registry_factory):
 from fastapi import HTTPException
 store=Procurement()
 @api.get('/api/v1/procurement/0903/objects',tags=['Procurement evidence'])
 def objects(kind:str|None=None,object_id:str|None=None):
  try:store.routes(registry_factory().revision)
  except RuntimeError as e:raise HTTPException(409,str(e)) from e
  result=store.query(kind,object_id);result['metadata']['city_revision']=registry_factory().revision
  return result
 @api.get('/api/v1/procurement/0903/works',tags=['Procurement evidence'])
 def works():return dict(works=store.read('works.json'),ordered_volumes_known=False)
 @api.get('/api/v1/procurement/0903/routes',tags=['Procurement evidence'])
 def routes(object_id:str|None=None):
  try:return store.routes(registry_factory().revision,object_id)
  except RuntimeError as e:raise HTTPException(409,str(e)) from e
 @api.get('/api/v1/procurement/0903/anchors',tags=['Procurement evidence'])
 def anchors():
  try:routes=store.routes(registry_factory().revision)
  except RuntimeError as e:raise HTTPException(409,str(e)) from e
  return dict(metadata=routes['metadata'],anchors=store.read('route-anchor-audit.json'))

 @api.post('/api/v1/procurement/0903/candidate-assessment',tags=['Procurement evidence'])
 def assess(payload:CandidateAssessment):
  try:
   reg=registry_factory();data=store.routes(reg.revision)
   candidate=next((f for f in data['features'] if f['id']==payload.candidate_id),None)
   if candidate is None:raise HTTPException(404,'Unknown route candidate')
   if candidate['properties']['route_status']!='all_anchors_candidate':raise ValueError('Partial route cannot be assessed as a continuous route')
   request=RouteRequest(profile=payload.profile,operation=payload.operation,valid_at=payload.valid_at,legs=[RouteLeg(**l) for l in candidate['properties']['legs']],territory_id=payload.territory_id,frame=payload.frame)
   state=meta=None
   if payload.territory_id:
    from datetime import datetime
    state,meta=api.state.dynamic_snow.state(payload.territory_id,payload.frame)
    if meta['city_revision']!=reg.revision:raise RuntimeError('Snow and graph revisions differ')
    if datetime.fromisoformat(meta['valid_at'].replace('Z','+00:00'))!=payload.valid_at:raise ValueError('Time must match the snow frame')
   result=EquipmentAccess(reg).route(request,state)
   return dict(candidate_id=candidate['id'],object_id=candidate['properties']['object_id'],assessment=result,snow_state=meta,
               service_boundary_confirmed=False,actual_building_accessibility='unknown',authorization_inferred=False)
  except RuntimeError as e:raise HTTPException(409,str(e)) from e
  except LookupError as e:raise HTTPException(404,str(e)) from e
  except ValueError as e:raise HTTPException(422,str(e)) from e
