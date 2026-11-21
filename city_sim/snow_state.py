"""snow-mass-fv-1: conservative surface saltation with explicit mobile inventory.

Not CFD or full SnowTran. Flux capacity follows Pomeroy–Gray (1990); limited
pickup, obstacle shelter and ageing are configurable research closures.
All internal transfers cancel pairwise in kg, including cleaning operations.
"""
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
import math

import numpy as np
from shapely.geometry import box, shape, LineString
from shapely.ops import transform
from weather_coverage.geography import TO_METRIC, TO_WGS84

VERSION = 'snow-mass-fv-1'


@dataclass
class SnowState:
    bounds: tuple  # metric EPSG:32657 west,south,east,north
    loose: np.ndarray  # kg/m² of open ground
    bound: np.ndarray
    area: np.ndarray  # actual open area in each cell, m²
    exposure: np.ndarray
    cleared_reference: np.ndarray
    density: float = 200.
    density_min: float = 100.
    density_max: float = 400.
    ledger: dict = field(default_factory=lambda: dict(initial_kg=0., precipitation_kg=0., boundary_in_kg=0.,
        boundary_out_kg=0., exported_kg=0., explicit_loss_kg=0., internal_cleaning_moved_kg=0.))
    parameters: dict = field(default_factory=lambda: dict(threshold_friction_m_s=.25, roughness_m=.001,
        air_density_kg_m3=1.225, saltation_constant_m_s=.68, pickup_scale_kg_m2=1.,
        ageing_per_hour=0., boundary='closed', max_step_seconds=60., cfl=.4))

    @property
    def dx(self): return (self.bounds[2]-self.bounds[0])/self.area.shape[1]
    @property
    def dy(self): return (self.bounds[3]-self.bounds[1])/self.area.shape[0]
    @property
    def mass(self): return self.loose+self.bound
    def total_kg(self): return float(np.sum(self.mass*self.area))
    def balance(self):
        l=self.ledger
        expected=l['initial_kg']+l['precipitation_kg']+l['boundary_in_kg']-l['boundary_out_kg']-l['exported_kg']-l['explicit_loss_kg']
        error=self.total_kg()-expected
        return {'actual_kg':self.total_kg(),'expected_kg':expected,'error_kg':error,
                'relative_error':abs(error)/max(1.,abs(expected),l['initial_kg']+l['precipitation_kg']+l['boundary_in_kg']),**l}

    def pack(self):
        stream=BytesIO()
        np.savez_compressed(stream,loose=self.loose,bound=self.bound,area=self.area,exposure=self.exposure,cleared_reference=self.cleared_reference)
        return stream.getvalue()

    @classmethod
    def unpack(cls, blob, meta):
        with np.load(BytesIO(blob)) as arrays:
            return cls(tuple(meta['metric_bounds']),**{k:arrays[k].copy() for k in arrays.files},
                       density=meta['density_kg_m3'],density_min=meta['density_min_kg_m3'],density_max=meta['density_max_kg_m3'],
                       ledger=dict(meta['ledger']),parameters=dict(meta['parameters']))

    def metadata(self):
        return {'model':VERSION,'metric_bounds':self.bounds,'crs':'EPSG:32657','shape':list(self.area.shape),
                'dx_m':self.dx,'dy_m':self.dy,'density_kg_m3':self.density,
                'density_min_kg_m3':self.density_min,'density_max_kg_m3':self.density_max,
                'ledger':self.ledger,'parameters':self.parameters,'balance':self.balance(),
                'parameter_status':'research_not_field_calibrated'}

    def cell(self,j,i):
        w,s,e,n=self.bounds
        return box(w+i*self.dx,n-(j+1)*self.dy,w+(i+1)*self.dx,n-j*self.dy)

    def overlaps(self, geometry):
        """Open cell area intersected with an operation, using stored ground geometry if available."""
        if geometry.is_empty: return np.zeros_like(self.area)
        w,s,e,n=self.bounds; x0,y0,x1,y1=geometry.bounds
        weights=np.zeros_like(self.area)
        il,ih=max(0,int(math.floor((x0-w)/self.dx))),min(self.area.shape[1]-1,int(math.floor((x1-w)/self.dx)))
        jl,jh=max(0,int(math.floor((n-y1)/self.dy))),min(self.area.shape[0]-1,int(math.floor((n-y0)/self.dy)))
        for j in range(jl,jh+1):
            for i in range(il,ih+1):
                if self.area[j,i]>0:
                    # Subcell ground shape is used when supplied by the domain adapter.
                    part=self.cell(j,i).intersection(geometry)
                    if hasattr(self,'ground'):
                        part=part.intersection(self.ground)
                        weights[j,i]=part.area
                    else:
                        weights[j,i]=part.area*self.area[j,i]/(self.dx*self.dy)
        return np.minimum(weights,self.area)

    def weather(self, seconds, wind_speed_m_s, wind_from_deg, snowfall_kg_m2_h=0.,
                boundary_inflow=None, explicit_loss_kg_m2_h=0.):
        if seconds<0 or wind_speed_m_s<0 or snowfall_kg_m2_h<0 or explicit_loss_kg_m2_h<0:
            raise ValueError('Negative weather input')
        p=self.parameters; active=self.area>0
        angle=math.radians(wind_from_deg)
        ux,uy=-math.sin(angle),math.cos(angle) # y is southward row coordinate
        local=wind_speed_m_s*self.exposure
        friction=.4*local/math.log(10/p['roughness_m'])
        threshold=p['threshold_friction_m_s']
        # A [m/s] rho_a/g (ut/u*) (u*²-ut²) = kg/(m s).
        capacity=p['saltation_constant_m_s']*p['air_density_kg_m3']/9.81*threshold/np.maximum(friction,1e-12)*np.maximum(friction**2-threshold**2,0)
        capacity=np.where(active,capacity,0.)
        effective_velocity=float(capacity.max(initial=0))/p['pickup_scale_kg_m2']
        dt_limit=min(p['max_step_seconds'], p['cfl']*min(self.dx,self.dy)/max(effective_velocity,1e-12))
        steps=max(1,math.ceil(seconds/dt_limit)); dt=seconds/steps
        incoming=boundary_inflow or {}
        if p['boundary']=='closed' and any(incoming.values()):
            raise ValueError('Inflow requires open boundaries')
        for _ in range(steps):
            precipitation=snowfall_kg_m2_h*dt/3600
            self.loose[active]+=precipitation
            self.ledger['precipitation_kg']+=float(precipitation*self.area.sum())
            q=capacity*np.minimum(1.,self.loose/p['pickup_scale_kg_m2'])
            # Directed outgoing mass per face. Both sides use the very same transfer.
            east=np.zeros_like(q); west=np.zeros_like(q); south=np.zeros_like(q); north=np.zeros_like(q)
            aperture=np.sqrt(np.clip(self.area/(self.dx*self.dy),0,1))
            common_x=np.minimum(aperture[:,:-1],aperture[:,1:])
            common_y=np.minimum(aperture[:-1,:],aperture[1:,:])
            if ux>=0: east[:,:-1]=q[:,:-1]*ux*self.dy*dt*common_x
            else: west[:,1:]=q[:,1:]*(-ux)*self.dy*dt*common_x
            if uy>=0: south[:-1,:]=q[:-1,:]*uy*self.dx*dt*common_y
            else: north[1:,:]=q[1:,:]*(-uy)*self.dx*dt*common_y
            if p['boundary']=='open':
                if ux>0: east[:,-1]=q[:,-1]*ux*self.dy*dt*aperture[:,-1]
                if ux<0: west[:,0]=q[:,0]*(-ux)*self.dy*dt*aperture[:,0]
                if uy>0: south[-1,:]=q[-1,:]*uy*self.dx*dt*aperture[-1,:]
                if uy<0: north[0,:]=q[0,:]*(-uy)*self.dx*dt*aperture[0,:]
            available=self.loose*self.area
            outgoing=east+west+south+north
            scale=np.minimum(1.,np.divide(available,outgoing,out=np.ones_like(q),where=outgoing>0))
            east*=scale;west*=scale;south*=scale;north*=scale
            change=-(east+west+south+north)
            change[:,1:]+=east[:,:-1];change[:,:-1]+=west[:,1:]
            change[1:,:]+=south[:-1,:];change[:-1,:]+=north[1:,:]
            self.ledger['boundary_out_kg']+=float(east[:,-1].sum()+west[:,0].sum()+south[-1,:].sum()+north[0,:].sum())
            for side,flux in incoming.items():
                if flux<0: raise ValueError('Negative boundary inflow')
                if side=='west': values=flux*self.dy*dt*aperture[:,0];change[:,0]+=values
                elif side=='east': values=flux*self.dy*dt*aperture[:,-1];change[:,-1]+=values
                elif side=='north': values=flux*self.dx*dt*aperture[0,:];change[0,:]+=values
                elif side=='south': values=flux*self.dx*dt*aperture[-1,:];change[-1,:]+=values
                else: raise ValueError('Unknown boundary side')
                self.ledger['boundary_in_kg']+=float(values.sum())
            self.loose+=np.divide(change,self.area,out=np.zeros_like(q),where=active)
            self.loose=np.maximum(self.loose,0.)
            loss=np.minimum(self.mass,explicit_loss_kg_m2_h*dt/3600)
            loose_loss=np.minimum(self.loose,loss);self.loose-=loose_loss;self.bound-=loss-loose_loss
            self.ledger['explicit_loss_kg']+=float(np.sum(loss*self.area))
            aged=self.loose*(-math.expm1(-p['ageing_per_hour']*dt/3600))
            self.loose-=aged;self.bound+=aged
        return {'steps':steps,'duration_seconds':seconds,'capacity_max_kg_m_s':float(capacity.max(initial=0)),**self.balance()}

    def clean(self, source_geometry, target_geometry=None, fraction=1., residual_kg_m2=0.,
              export_fraction=0., bank_mobile_fraction=.2):
        src=self.overlaps(source_geometry)
        share=np.divide(src,self.area,out=np.zeros_like(src),where=self.area>0)
        remove_density=np.maximum(self.mass-residual_kg_m2,0)*fraction*share
        removed_kg=float(np.sum(remove_density*self.area))
        deposited=removed_kg*(1-export_fraction)
        if deposited>1e-10 and target_geometry is not None and target_geometry.difference(box(*self.bounds)).area>1e-6*max(1.,target_geometry.area):
            raise ValueError('Destination extends outside territory: specify an in-domain destination and explicit exported fraction')
        target=self.overlaps(target_geometry) if target_geometry is not None else np.zeros_like(src)
        if deposited>1e-10 and target.sum()<=0:
            raise ValueError('Moving snow requires a nonempty open destination; no snow is discarded')
        ratio=np.divide(remove_density,self.mass,out=np.zeros_like(src),where=self.mass>0)
        self.loose*=1-ratio;self.bound*=1-ratio
        if deposited:
            added_kg=deposited*target/target.sum()
            addition=np.divide(added_kg,self.area,out=np.zeros_like(src),where=self.area>0)
            self.loose+=addition*bank_mobile_fraction;self.bound+=addition*(1-bank_mobile_fraction)
        # The reference is the completed operation, including same-cell bank deposits.
        self.cleared_reference=np.where(src>0,self.mass,self.cleared_reference)
        self.ledger['exported_kg']+=removed_kg*export_fraction
        self.ledger['internal_cleaning_moved_kg']+=deposited
        return {'removed_kg':removed_kg,'bank_deposited_kg':deposited,'exported_kg':removed_kg*export_fraction,
                'source_open_area_m2':float(src.sum()),'target_open_area_m2':float(target.sum()),**self.balance()}


def operation_geometry(payload):
    """Explicit polygons, or a directed service line with explicitly provided widths."""
    if payload.get('geometry'):
        source=transform(TO_METRIC,shape(payload['geometry']))
    elif payload.get('service_line') and payload.get('width_m'):
        line=transform(TO_METRIC,shape(payload['service_line']))
        if line.geom_type!='LineString': raise ValueError('Expected a directed LineString')
        source=line.buffer(payload['width_m']/2,cap_style=2)
    else:
        raise ValueError('Supply service polygon or directed service_line and explicit width_m')
    target=transform(TO_METRIC,shape(payload['destination'])) if payload.get('destination') else None
    if target is None and payload.get('bank_side') and payload.get('service_line'):
        if not payload.get('bank_width_m') or not payload.get('width_m'):
            raise ValueError('Bank derivation requires explicit service and bank widths')
        line=transform(TO_METRIC,shape(payload['service_line']))
        sign=1 if payload['bank_side']=='left' else -1
        offset=line.offset_curve(sign*(payload['width_m']+payload['bank_width_m'])/2)
        target=offset.buffer(payload['bank_width_m']/2,cap_style=2)
    if not source.is_valid or source.is_empty or (target is not None and (not target.is_valid or target.is_empty)):
        raise ValueError('Invalid operation geometry')
    return source,target
