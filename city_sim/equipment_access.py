"""Conservative three-state equipment screening over actual OSM node edges.

OSM widths, lane estimates and building gaps are different evidence classes.
Scenario profiles and snow thresholds are not manufacturer specifications.
"""
import math
import re
from collections import Counter
from datetime import datetime
from typing import Literal
import numpy as np
from pydantic import Field, AwareDatetime, model_validator
from shapely.geometry import LineString, Point, Polygon, shape, mapping, box
from shapely.ops import transform, unary_union
from weather_coverage.geography import TO_METRIC, TO_WGS84, tags
from .dynamic_schema import Strict
from .spatial import RasterAssessment


def metres(value):
    if value is None:
        return None
    text = str(value).strip()
    match = re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*(m|metres|meters|ft)?',text,re.I)
    if match:
        n = float(match[1].replace(',','.'))*(.3048 if (match[2] or '').lower()=='ft' else 1)
        return n if 0 < n <= 100 else None
    match = re.fullmatch(r"(\d+)'\s*(\d+(?:\.\d+)?)?\"?",text)
    if match:
        n = int(match[1])*.3048+float(match[2] or 0)*.0254
        return n if 0<n<=100 else None
    return None


def grade(value):
    if value is None:
        return None
    match = re.fullmatch(r'([+-]?\d+(?:\.\d+)?)\s*(%|°)?',str(value))
    if not match:
        return None
    n = float(match[1])
    return math.tan(math.radians(n))*100 if match[2]=='°' else n


def combine(statuses):
    return 'blocked' if 'blocked' in statuses else 'unknown' if 'unknown' in statuses else 'passable'


class Equipment(Strict):
    id: str = Field(min_length=1,max_length=100)
    source: str = Field(min_length=1,max_length=1000)
    specification_status: Literal['scenario','manufacturer','measured']='scenario'
    width_m: float = Field(gt=0,le=10)
    length_m: float = Field(gt=0,le=30)
    height_m: float = Field(gt=0,le=10)
    working_width_m: float = Field(gt=0,le=15)
    side_clearance_m: float = Field(.15,ge=0,le=3)
    turning_radius_m: float = Field(gt=0,le=50)
    max_grade_pct: float = Field(gt=0,le=60)
    snow_obstruction_depth_m: float = Field(.25,gt=0,le=3)
    pedestrian_area_capable: bool = False


PROFILES = [Equipment(id=key,source='Research class example, not a real fleet or certified specification',
    width_m=w,length_m=l,height_m=h,working_width_m=work,turning_radius_m=r,max_grade_pct=g,
    pedestrian_area_capable=foot) for key,w,l,h,work,r,g,foot in (
        ('sidewalk',1.1,2.5,2.,1.3,1.5,15,True),('compact',1.8,4.,2.5,2.2,3.,12,True),
        ('truck',2.55,8.,3.5,3.,7.,10,False))]


class WayEvidence(Strict):
    way_id: str
    source: str = Field(min_length=1,max_length=1000)
    kind: Literal['survey','scenario']
    valid_from: AwareDatetime
    valid_to: AwareDatetime
    width_min_m: float|None = Field(None,gt=0,le=100)
    width_max_m: float|None = Field(None,gt=0,le=100)
    corridor: dict|None = None
    grade_pct: float|None = Field(None,ge=-100,le=100)
    legal_access: bool|None = None
    work_allowed: bool|None = None
    obstacles_reviewed: bool = False
    @model_validator(mode='after')
    def check(self):
        if self.valid_to <= self.valid_from:
            raise ValueError('Evidence validity interval must be nonempty')
        if (self.width_min_m is None)!=(self.width_max_m is None):
            raise ValueError('Supply both width interval bounds')
        if self.width_min_m is not None and self.width_max_m < self.width_min_m:
            raise ValueError('Inverted width interval')
        if self.corridor is not None:
            try:
                g = shape(self.corridor)
            except Exception as e:
                raise ValueError('Invalid corridor') from e
            if not g.is_valid or g.is_empty or g.geom_type not in {'Polygon','MultiPolygon'}:
                raise ValueError('Corridor must be a valid polygon')
            w,s,e,n = g.bounds
            if not -180<=w<e<=180 or not -80<s<n<80 or e-w>1 or n-s>1:
                raise ValueError('Invalid/oversized corridor coordinates')
        return self


class RouteLeg(Strict):
    way_id: str
    from_node: str
    to_node: str


class RouteRequest(Strict):
    profile: Equipment
    valid_at: AwareDatetime
    operation: Literal['transit','clear']='transit'
    legs: list[RouteLeg] = Field(min_length=1,max_length=2000)
    evidence: list[WayEvidence] = Field(default_factory=list,max_length=2000)
    territory_id: str|None = None
    frame: int|None = Field(None,ge=0)
    entrance_id: str|None = None
    @model_validator(mode='after')
    def unique(self):
        ids = [e.way_id for e in self.evidence]
        if len(set(ids)) != len(ids):
            raise ValueError('Conflicting evidence for one way; reconcile explicitly')
        if self.frame is not None and self.territory_id is None:
            raise ValueError('frame requires territory_id')
        return self


def width_evidence(meta):
    width = metres(meta.get('width'))
    estimated = metres(meta.get('est_width'))
    lanes = metres(meta.get('lanes'))
    if width:
        return dict(interval_m=[width*.8,width*1.2],nominal_m=width,source='OSM:width',
                    status='mapped_not_surveyed',uncertainty='research_20pct_not_statistical')
    if estimated:
        return dict(interval_m=[estimated*.6,estimated*1.4],nominal_m=estimated,source='OSM:est_width',
                    status='estimated',uncertainty='research_40pct_not_statistical')
    if lanes and lanes.is_integer() and lanes<=12 and meta.get('highway') not in {'footway','steps','path','pedestrian','cycleway'}:
        return dict(interval_m=[lanes*2.5,lanes*3.75],nominal_m=None,source='algorithm:OSM_lanes',
                    status='estimated',uncertainty='scenario_lane_width_2_5_to_3_75m_excludes_parking')
    return dict(interval_m=None,nominal_m=None,source=None,status='unknown',uncertainty='no_measurement')


def continuous_width(section, sampler, density, threshold):
    """Longest clear interval in a section, retaining missing support as unknown."""
    longest = current = 0.; known = 0.
    for part, length, mass in sampler._parts(section):
        if mass is not None:
            known += length
        if mass is not None and mass/density <= threshold:
            current += length; longest = max(longest,current)
        else:
            current = 0.
    return longest, known/max(section.length,1e-12)


def snow_width(line, width, state, threshold):
    """Cross-section minimum; never equate a width estimate to a surveyed corridor."""
    sampler = RasterAssessment(np.where(state.area>0,state.mass,np.nan),state.bounds,metric_coordinates=True)
    count = min(4096,max(2,math.ceil(line.length/min(1.,state.dx/2,state.dy/2))+1))
    lo = hi = width; coverage = 1.
    for distance in np.linspace(0,line.length,count):
        p = line.interpolate(distance)
        a = line.interpolate(max(0,distance-.1)); b = line.interpolate(min(line.length,distance+.1))
        norm = a.distance(b)
        if norm == 0:
            continue
        nx,ny = -(b.y-a.y)/norm,(b.x-a.x)/norm
        section = LineString([(p.x-nx*width/2,p.y-ny*width/2),(p.x+nx*width/2,p.y+ny*width/2)])
        low,cov = continuous_width(section,sampler,state.density_min,threshold)
        high,_ = continuous_width(section,sampler,state.density_max,threshold)
        lo=min(lo,low);hi=min(hi,high);coverage=min(coverage,cov)
        if hasattr(state,'ground') and not state.ground.buffer(1e-6).covers(section):
            coverage=0.
    return dict(available_width_range_m=[lo,hi],coverage_fraction=coverage,cross_sections=count,
                method='minimum_longest_contiguous_clear_cross_section',threshold_m=threshold,
                grid_dx_m=state.dx,grid_dy_m=state.dy,field_validated=False)


class EquipmentAccess:
    def __init__(self, registry):
        self.registry = registry
        self.elements = registry.catalog_city_elements
        self.roads = {f['id']:f for f in registry.catalog.roads.features}
        self.restrictions=[e for e in self.elements.values() if e.tag=='relation' and tags(e).get('type')=='restriction']

    def inventory(self):
        features = []; widths=Counter(); length=Counter(); constraints=Counter()
        for road,geom in zip(self.registry.catalog.roads.features,self.registry.catalog.metric_roads):
            key='w'+road['id'].split(':')[-1]; e=self.elements[key]; meta=tags(e)
            w=width_evidence(meta); widths[w['status']]+=1;length[w['status']]+=geom.length
            refs=['n'+n.get('ref') for n in e.findall('nd')]
            barriers=[{'node_id':'osm:node:'+n[1:],'tags':tags(self.elements[n])} for n in refs
                      if n in self.elements and tags(self.elements[n]).get('barrier')]
            for k in ('width','est_width','lanes','incline','maxwidth','maxwidth:physical','maxheight','maxweight'):
                if k in meta:constraints[k]+=1
            # Building gap is an upper bound on open space, never a carriageway width.
            p=geom.interpolate(.5,normalized=True)
            indices=self.registry.catalog.building_index.query(p.buffer(25),predicate='intersects')
            gap=2*min((p.distance(self.registry.catalog.metric_buildings[i]) for i in indices),default=25.)
            features.append({**road,'properties':{**road['properties'],'width_evidence':w,
                'building_gap_upper_bound_at_midpoint_m':gap,'gap_is_road_width':False,'barriers':barriers,
                'raw_constraints':{k:v for k,v in meta.items() if k.startswith(('max','access','vehicle','motor','incline','restriction'))},
                'confidence':'OSM_snapshot_not_field_verified'}})
        return dict(type='FeatureCollection',features=features,metadata=dict(revision=self.registry.revision,
            ways=len(features),width_counts=dict(widths),length_m_by_width_status=dict(length),constraint_tag_counts=dict(constraints),
            surveyed_widths=0,limitations=['Lane estimates and building gaps cannot certify passage','No imagery width inferred without licensed calibrated segmentation']))

    def assess_leg(self, leg, profile, operation, evidence=None, state=None):
        road=self.roads.get(leg.way_id)
        if road is None:
            raise LookupError('Unknown way '+leg.way_id)
        e=self.elements['w'+leg.way_id.split(':')[-1]]; meta=tags(e)
        refs=['osm:node:'+n.get('ref') for n in e.findall('nd')]
        pair=(leg.from_node,leg.to_node)
        directed=list(zip(refs,refs[1:]))
        forward=pair in directed
        if not forward and pair[::-1] not in directed:
            raise ValueError('Route leg is not an existing consecutive OSM node pair')
        a,b=(self.elements['n'+n.split(':')[-1]] for n in pair)
        line=transform(TO_METRIC,LineString([(float(n.get('lon')),float(n.get('lat'))) for n in (a,b)]))
        if line.length<=0:
            raise ValueError('Zero-length route leg')
        reasons=[]; geometric=[]; legal=[]; work=[]
        width=width_evidence(meta)
        if evidence and evidence.width_min_m is not None:
            width=dict(interval_m=[evidence.width_min_m,evidence.width_max_m],source=evidence.source,
                       status=evidence.kind,nominal_m=None,uncertainty='provided_interval')
        interval=width['interval_m']; required=max(profile.width_m,profile.working_width_m if operation=='clear' else 0)+2*profile.side_clearance_m
        credible=width['status'] in {'survey','scenario','mapped_not_surveyed'}
        snow=None
        if interval:
            if state is not None:
                low=snow_width(line,interval[0],state,profile.snow_obstruction_depth_m)
                high=snow_width(line,interval[1],state,profile.snow_obstruction_depth_m)
                interval=[low['available_width_range_m'][0],high['available_width_range_m'][1]]
                snow={**low,'available_width_range_m':interval,'coverage_fraction':min(low['coverage_fraction'],high['coverage_fraction'])}
            if interval[1]<required and credible and (snow is None or snow['coverage_fraction']>=.999999):
                geometric.append('blocked');reasons.append('available_width_below_required')
            elif interval[0]>=required and credible and (snow is None or snow['coverage_fraction']>=.999999):
                geometric.append('passable')
            else:
                geometric.append('unknown');reasons.append('width_or_snow_uncertainty')
        else:
            geometric.append('unknown');reasons.append('missing_width')
        for key,needed in (('maxwidth:physical',required),('maxheight',profile.height_m)):
            cap=metres(meta.get(key))
            if cap and cap<needed:
                geometric.append('blocked');reasons.append(key)
        cap=metres(meta.get('maxwidth'))
        if cap and profile.width_m>cap:
            legal.append('blocked');reasons.append('legal_maxwidth')
        access=meta.get('motor_vehicle',meta.get('vehicle',meta.get('access')))
        if access=='no':
            legal.append('blocked');reasons.append('motor_access_no')
        elif access in {'private','destination','delivery','agricultural','forestry','permit'}:
            legal.append('unknown');reasons.append('access_requires_authorization')
        if evidence and evidence.legal_access is not None:
            legal.append('passable' if evidence.legal_access else 'blocked')
        else:
            legal.append('unknown')
        for key in ('maxlength','maxweight','maxaxleload'):
            if key in meta:
                if key=='maxlength' and metres(meta[key]) is not None and profile.length_m>metres(meta[key]):
                    legal.append('blocked');reasons.append('legal_maxlength')
                else:
                    legal.append('unknown');reasons.append('unresolved_'+key)
        if any('conditional' in key for key in meta):
            legal.append('unknown');reasons.append('conditional_restriction_requires_review')
        one=meta.get('oneway','yes' if meta.get('junction')=='roundabout' else 'no')
        if (one in {'yes','1','true'} and not forward) or (one=='-1' and forward):
            legal.append('blocked');reasons.append('oneway')
        if meta['highway']=='steps':
            geometric.append('blocked');reasons.append('stairs')
        elif road['properties']['mode']=='pedestrian' and not profile.pedestrian_area_capable:
            geometric.append('blocked');reasons.append('profile_not_suitable_for_pedestrian_area')
        slope=evidence.grade_pct if evidence and evidence.grade_pct is not None else grade(meta.get('incline'))
        if slope is None:
            geometric.append('unknown');reasons.append('unknown_grade')
        elif abs(slope)>profile.max_grade_pct:
            geometric.append('blocked');reasons.append('grade_limit')
        barrier_rows=[]
        for node in (a,b):
            t=tags(node)
            for key,needed in (('maxwidth:physical',required),('maxheight',profile.height_m)):
                cap=metres(t.get(key))
                if cap is not None and cap<needed:
                    geometric.append('blocked');reasons.append('node_'+key)
            if metres(t.get('maxwidth')) is not None and profile.width_m>metres(t['maxwidth']):
                legal.append('blocked');reasons.append('node_legal_maxwidth')
            if not t.get('barrier'):
                continue
            opening=metres(t.get('maxwidth:physical')) or metres(t.get('width'))
            status='blocked' if opening and opening<required else 'unknown'
            if t.get('barrier') in {'wall','fence','block'}:
                status='blocked'
            if t.get('locked')=='yes' or t.get('motor_vehicle',t.get('vehicle',t.get('access')))=='no':
                status='blocked'
            geometric.append(status);reasons.append('node_barrier_'+t['barrier'])
            barrier_rows.append(dict(node_id='osm:node:'+node.get('id'),tags=t,status=status))
        if not evidence or not evidence.obstacles_reviewed:
            geometric.append('unknown');reasons.append('obstacles_not_reviewed')
        corridor=transform(TO_METRIC,shape(evidence.corridor)) if evidence and evidence.corridor else None
        swept=line.buffer(required/2,cap_style=2)
        cat=self.registry.catalog
        if state is not None:
            sampler=RasterAssessment(np.where(state.area>0,state.mass,np.nan),state.bounds,metric_coordinates=True)
            support=sampler.assess(swept,profile.snow_obstruction_depth_m*state.density_min,False)
            # Wide but disconnected clear cross sections do not establish a route.
            # Check the entire straight swept strip against every intersecting cell.
            if support['coverage_fraction']<.999999 or support['max_cm'] is None or support['max_cm']>profile.snow_obstruction_depth_m*state.density_min:
                geometric.append('unknown');reasons.append('continuous_centerline_snow_clearance_not_established')
            if hasattr(state,'ground') and not state.ground.buffer(1e-6).covers(swept):
                geometric.append('unknown');reasons.append('swept_strip_outside_open_ground')
        if any(cat.metric_buildings[i].intersection(swept).area>1e-3
               for i in cat.building_index.query(swept,predicate='intersects')):
            geometric.append('unknown');reasons.append('centerline_sweep_intersects_building')
        if corridor is not None:
            if not corridor.buffer(1e-6).covers(swept):
                geometric.append('unknown');reasons.append('centerline_sweep_outside_corridor_alternative_path_unknown')
        work.append('passable' if operation=='transit' or evidence and evidence.work_allowed is True else
                    'blocked' if evidence and evidence.work_allowed is False else 'unknown')
        statuses=dict(geometric=combine(geometric),legal=combine(legal),work=combine(work))
        return dict(way_id=leg.way_id,from_node=leg.from_node,to_node=leg.to_node,status=combine(statuses.values()),
            **statuses,reasons=reasons,width_evidence=width,available_width_range_m=interval,required_width_m=required,
            snow=snow,barriers=barrier_rows,grade_pct=slope,
            geometry=mapping(transform(TO_WGS84,line)),corridor=mapping(transform(TO_WGS84,corridor)) if corridor is not None else None)

    def route(self, request, state=None):
        evidence={e.way_id:e for e in request.evidence}
        if any(not e.valid_from<=request.valid_at<e.valid_to for e in evidence.values()):
            raise ValueError('Evidence not valid at route time')
        legs=[]
        for i,leg in enumerate(request.legs):
            if i and request.legs[i-1].to_node!=leg.from_node:
                raise ValueError('Disconnected route; geometric snapping is not allowed')
            legs.append(self.assess_leg(leg,request.profile,request.operation,evidence.get(leg.way_id),state))
        turns=[]
        for i,(a,b) in enumerate(zip(legs,legs[1:])):
            l1,l2=(transform(TO_METRIC,shape(r['geometry'])) for r in (a,b))
            v1=np.array(l1.coords[-1])-np.array(l1.coords[0]); v2=np.array(l2.coords[-1])-np.array(l2.coords[0])
            angle=math.acos(float(np.clip(np.dot(v1,v2)/(l1.length*l2.length),-1,1)))
            tangent=request.profile.turning_radius_m*math.tan(min(angle,math.pi-1e-6)/2)
            status='passable' if angle<1e-5 else 'unknown'
            reasons=[] if status=='passable' else ['turn_swept_envelope_not_certified']
            if tangent>min(l1.length,l2.length):
                reasons.append('nominal_radius_tangent_exceeds_leg')
            elif angle>=1e-5 and a['corridor'] and b['corridor']:
                # Circular fillet with body bounded by a circumscribed disc.
                # This is deliberately conservative; it can establish one feasible
                # sweep, but failure does not prove all trajectories impossible.
                joint=np.array(l1.coords[-1]);u=v1/l1.length;v=v2/l2.length
                sign=1 if np.cross(np.append(u,0),np.append(v,0))[2]>0 else -1
                start=joint-u*tangent;end=joint+v*tangent
                center=start+sign*np.array([-u[1],u[0]])*request.profile.turning_radius_m
                theta=math.atan2(start[1]-center[1],start[0]-center[0])
                points=[start]+[center+request.profile.turning_radius_m*np.array([math.cos(t),math.sin(t)])
                    for t in np.linspace(theta,theta+sign*angle,max(4,math.ceil(angle/.02)))]+[end]
                arc=LineString(points)
                radius=math.hypot(request.profile.length_m/2,max(request.profile.width_m,
                    request.profile.working_width_m if request.operation=='clear' else 0)/2)+request.profile.side_clearance_m+.02
                corridor=unary_union([transform(TO_METRIC,shape(a['corridor'])),transform(TO_METRIC,shape(b['corridor']))])
                sweep=arc.buffer(radius)
                if corridor.buffer(1e-6).covers(sweep) and state is None:
                    status='passable';reasons=['conservative_circular_sweep_inside_supplied_corridor']
                # Snow free-space at a turn needs the 2-D swept support, not only
                # cross sections of the two straight segments.
            # OSM via-node restrictions, including only_* restrictions.
            for e in self.restrictions:
                t=tags(e)
                members={(m.get('role'),m.get('type')):m.get('ref') for m in e.findall('member')}
                if ('via','way') in members and members.get(('via','way')) in {a['way_id'].split(':')[-1],b['way_id'].split(':')[-1]}:
                    if status=='passable':status='unknown'
                    reasons.append('via_way_restriction_requires_review')
                if members.get(('from','way'))!=a['way_id'].split(':')[-1] or members.get(('via','node'))!=a['to_node'].split(':')[-1]:
                    continue
                target=members.get(('to','way')); rule=t.get('restriction','')
                if any('conditional' in key for key in t):
                    status=combine([status,'unknown']);reasons.append('conditional_turn_restriction_requires_review')
                if (rule.startswith('no_') and target==b['way_id'].split(':')[-1]) or (rule.startswith('only_') and target!=b['way_id'].split(':')[-1]):
                    status=combine([status,'unknown' if t.get('except') or any('conditional' in key for key in t) else 'blocked'])
                    reasons.append('OSM_turn_restriction')
            turns.append(dict(at_node=a['to_node'],angle_deg=math.degrees(angle),required_tangent_m=tangent,status=status,reasons=reasons))
        # Consecutive fillets must not consume overlapping lengths of an inner leg.
        for i in range(1,len(legs)-1):
            length=transform(TO_METRIC,shape(legs[i]['geometry'])).length
            if turns[i-1]['required_tangent_m']+turns[i]['required_tangent_m']>length:
                for t in (turns[i-1],turns[i]):
                    if t['status']=='passable':
                        t['status']='unknown'
                    t['reasons'].append('adjacent_turn_tangents_overlap')
        endpoint=None
        if request.entrance_id:
            obj=self.registry.objects.get(request.entrance_id)
            if obj is None or obj['properties']['kind']!='entrance':
                raise LookupError('Mapped entrance not found')
            terminal='n'+request.legs[-1].to_node.split(':')[-1]
            connected=request.entrance_id=='osm:node:'+terminal[1:]
            endpoint=dict(entrance_id=request.entrance_id,status='unknown',topologically_connected=connected,
                          evidence='shared_osm_node' if connected else 'no_topological_connection',door_clearance='unknown')
        status=combine([r['status'] for r in legs]+[t['status'] for t in turns]+([endpoint['status']] if endpoint else []))
        return dict(revision=self.registry.revision,status=status,legs=legs,turns=turns,entrance_connection=endpoint,
            profile=request.profile.model_dump(),valid_at=request.valid_at.isoformat(),operation=request.operation,
            scenario_only=request.profile.specification_status=='scenario' or any(e.kind=='scenario' for e in evidence.values()) or state is not None or any(r['width_evidence']['status']!='survey' for r in legs),
            actual_operational_access='unknown',synthetic_connections=False,
            limitations=['Screening is not a manoeuvre planner; turns need a supplied corridor for a conservative circular sweep',
                        'Snow width uses cell-constant mass and density bounds, not street measurements',
                        'Cross-section width is sampled; complete straight swept strip is also checked',
                        'Weight, axle, conditional and via-way restrictions require additional review',
                        'GPS traversal does not create a cleaning event'])
