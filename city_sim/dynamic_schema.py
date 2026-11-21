"""Versioned dynamic API inputs, explicit units and source/confirmation semantics."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator
from shapely.geometry import shape

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)

class Parameters(Strict):
    threshold_friction_m_s: float=Field(.25,gt=0,le=2)
    roughness_m: float=Field(.001,gt=0,lt=1)
    air_density_kg_m3: float=Field(1.225,gt=0,le=2)
    saltation_constant_m_s: float=Field(.68,ge=0,le=5)
    pickup_scale_kg_m2: float=Field(1.,gt=0,le=100)
    ageing_per_hour: float=Field(0.,ge=0,le=10)
    boundary: Literal['closed','open']='closed'
    max_step_seconds: float=Field(60.,gt=0,le=600)
    cfl: float=Field(.4,gt=0,le=.5)

class InitialState(Strict):
    bounds: tuple[float,float,float,float]
    rows: int=Field(64,ge=2,le=256)
    cols: int=Field(64,ge=2,le=256)
    valid_at: AwareDatetime
    loose_kg_m2: float|list[list[float]]
    bound_kg_m2: float|list[list[float]]=0.
    density_kg_m3: float=Field(200.,gt=0,le=917)
    density_min_kg_m3: float=Field(100.,gt=0,le=917)
    density_max_kg_m3: float=Field(400.,gt=0,le=917)
    source: str=Field(min_length=1,max_length=1000)
    confirmation: Literal['confirmed','scenario','unconfirmed']='scenario'
    city_geometry: bool=True
    parameters: Parameters=Field(default_factory=Parameters)
    patches: list[dict]=Field(default_factory=list,max_length=1000)
    @model_validator(mode='after')
    def check(self):
        w,s,e,n=self.bounds
        if not -180<=w<e<=180 or not -80<s<n<80 or e-w>1 or n-s>1: raise ValueError('Invalid/oversized geographic bounds')
        if not self.density_min_kg_m3<=self.density_kg_m3<=self.density_max_kg_m3: raise ValueError('Density interval must contain nominal density')
        import numpy as np
        for mass in (self.loose_kg_m2,self.bound_kg_m2):
            a=np.asarray(mass,dtype=float)
            if a.ndim and a.shape!=(self.rows,self.cols): raise ValueError('Initial grid dimensions differ')
            if not np.isfinite(a).all() or (a<0).any(): raise ValueError('Initial mass must be finite and nonnegative; missing is not zero')
        for patch in self.patches:
            if set(patch)-{'geometry','loose_kg_m2','bound_kg_m2'}: raise ValueError('Unknown initial patch field')
            try:
                g=shape(patch['geometry'])
            except Exception as error:
                raise ValueError('Invalid initial patch geometry') from error
            if not g.is_valid or g.is_empty or g.geom_type not in {'Polygon','MultiPolygon'}: raise ValueError('Initial patch must be a valid polygon')
            for k in ('loose_kg_m2','bound_kg_m2'):
                if k in patch:
                    try:
                        value=float(patch[k])
                    except (TypeError,ValueError) as error:
                        raise ValueError('Invalid patch mass') from error
                    if not np.isfinite(value) or value<0: raise ValueError('Invalid patch mass')
                    patch[k]=value
        return self

class WeatherEvent(Strict):
    kind: Literal['weather']='weather'
    event_id: str=Field(min_length=1,max_length=128,pattern=r'^[\w.:-]+$')
    start_at: AwareDatetime
    end_at: AwareDatetime
    wind_speed_m_s: float=Field(ge=0,le=70)
    wind_from_deg: float=Field(ge=0,lt=360)
    snowfall_kg_m2_h: float=Field(0.,ge=0,le=100)
    explicit_loss_kg_m2_h: float=Field(0.,ge=0,le=100)
    boundary_inflow_kg_m_s: dict[Literal['west','east','north','south'],float]=Field(default_factory=dict)
    source: str=Field(min_length=1,max_length=1000)
    retrieved_at: AwareDatetime|None=None
    source_resolution_m: float|None=Field(None,gt=0)
    source_coordinates: tuple[float,float]|None=None
    shelter: bool=True
    unknown_building_height_m: float|None=Field(None,gt=0,le=100)
    @model_validator(mode='after')
    def check(self):
        if not 0<(self.end_at-self.start_at).total_seconds()<=86400: raise ValueError('Weather duration must be in (0,24h]')
        if any(v<0 for v in self.boundary_inflow_kg_m_s.values()): raise ValueError('Boundary inflow must be nonnegative')
        return self

class CleaningEvent(Strict):
    kind: Literal['cleaning']='cleaning'
    event_id: str=Field(min_length=1,max_length=128,pattern=r'^[\w.:-]+$')
    valid_at: AwareDatetime
    operation: Literal['road_plow','sidewalk_clear','snowbank','load_export','manual_clear','repeat_clear']
    geometry: dict|None=None
    service_line: dict|None=None
    width_m: float|None=Field(None,gt=0,le=100)
    destination: dict|None=None
    bank_side: Literal['left','right']|None=None
    bank_width_m: float|None=Field(None,gt=0,le=100)
    fraction: float=Field(1.,ge=0,le=1)
    residual_kg_m2: float=Field(0.,ge=0)
    export_fraction: float=Field(0.,ge=0,le=1)
    bank_mobile_fraction: float=Field(.2,ge=0,le=1)
    source: str=Field(min_length=1,max_length=1000)
    confirmation: Literal['confirmed','scenario','unconfirmed']='unconfirmed'
    order: int=Field(0,ge=0)
    related_object_ids: list[str]=Field(default_factory=list,max_length=100)
    @model_validator(mode='after')
    def check(self):
        for name,types in [('geometry',{'Polygon','MultiPolygon'}),('destination',{'Polygon','MultiPolygon'}),('service_line',{'LineString'})]:
            value=getattr(self,name)
            if value:
                geom=shape(value)
                if not geom.is_valid or geom.is_empty or geom.geom_type not in types: raise ValueError('Invalid '+name)
        if not self.geometry and not (self.service_line and self.width_m): raise ValueError('Operation footprint requires polygon or line and explicit width')
        return self
