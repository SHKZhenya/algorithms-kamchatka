"""Categorize unsupported raster/object portions; missing is never imputed as zero."""
from collections import Counter
import math
import numpy as np
from shapely import STRtree
from shapely.geometry import Point,shape,box
from shapely.ops import transform,unary_union
from weather_coverage.geography import TO_METRIC
from .spatial import RasterAssessment


def diagnose_old_raster(registry,grid,bounds,city,kind='road',tile_cells=64):
    raster=RasterAssessment(grid,bounds)
    water=[shape(f['geometry']) for f in city.osm_layers[1]['features'] if f['properties'].get('kind')=='water']
    water_index=STRtree(water)
    building_index=registry.catalog.building_index
    from .city_snow import administrative_boundary
    city_boundary=administrative_boundary()
    if kind=='road':assets=[(f,shape(f['geometry'])) for f in registry.catalog.roads.features]
    elif kind=='building':
        assets=[]
        from weather_coverage.geography import TO_WGS84
        for f in registry.building_records:
            b=transform(TO_METRIC,shape(f['geometry']));a=b.buffer(8).difference(b)
            blockers=building_index.query(a,predicate='intersects')
            if len(blockers):a=a.difference(unary_union([registry.catalog.metric_buildings[i] for i in blockers]))
            assets.append((f,transform(TO_WGS84,a)))
    else:assets=[(f,registry.areas[f['id']]) for f in registry.objects.values() if f['properties']['kind']=='entrance']
    categories=Counter();unknown_objects=0;total=covered=0.
    native_centers={}
    nx,ny=raster.cols/tile_cells,raster.rows/tile_cells
    xcorners=np.linspace(raster.w,raster.e,int(nx)+1)
    ycorners=np.linspace(raster.n,raster.s,int(ny)+1)
    for f,geom in assets:
        result=raster.assess(geom,20,False);total+=result['total'];covered+=result['covered']
        unknown_objects+=not bool(result['covered'])
        if geom.is_empty:categories['missing_or_empty_support_geometry']+=1;continue
        for part,weight,value in raster._parts(geom):
            if value is not None:continue
            p=part.representative_point()
            if not (raster.w<=p.x<raster.e and raster.s<p.y<=raster.n):reason='outside_raster_extent'
            elif not city_boundary.covers(p):reason='outside_legacy_city_boundary'
            elif len(water_index.query(p,predicate='intersects')):reason='water_geometry'
            elif len(building_index.query(Point(TO_METRIC(p.x,p.y)),predicate='intersects')):reason='building_geometry'
            else:
                i=min(raster.cols-1,int((p.x-raster.w)/raster.dx));j=min(raster.rows-1,int((raster.n-p.y)/raster.dy))
                center=Point(raster.w+(i+.5)*raster.dx,raster.n-(j+.5)*raster.dy)
                if not city_boundary.covers(center):
                    reason='legacy_boundary_cell_center_alias'
                elif len(building_index.query(Point(TO_METRIC(center.x,center.y)),predicate='intersects')) or len(water_index.query(center,predicate='intersects')):
                    reason='cell_center_mask_alias_insufficient_resolution'
                else:
                    # Danil uses aspect-preserving native rows/cols, remapped to square tiles.
                    # Reconstruct the sampled native center before assigning an unexplained gap.
                    key=(i,j)
                    if key not in native_centers:
                        ti,tj=i//tile_cells,j//tile_cells
                        tw,te=float(xcorners[ti]),float(xcorners[ti+1])
                        tn,ts=float(ycorners[tj]),float(ycorners[tj+1])
                        sy=math.pi*6378137/180;sx=sy*math.cos(math.radians((ts+tn)/2))
                        width,height=(te-tw)*sx,(tn-ts)*sy;cell=max(width,height)/tile_cells
                        nc,nr=math.ceil(width/cell),math.ceil(height/cell)
                        ni=min(nc-1,int(((i%tile_cells)+.5)*nc/tile_cells));nj=min(nr-1,int(((j%tile_cells)+.5)*nr/tile_cells))
                        native_centers[key]=Point(TO_METRIC(tw+(ni+.5)*(te-tw)/nc,tn-(nj+.5)*(tn-ts)/nr))
                    if len(building_index.query(native_centers[key],predicate='intersects')):
                        reason='native_grid_resampling_building_mask_alias'
                    else:reason='uncomputed_domain_or_other_mask_unknown'
            categories[reason]+=weight
    return {'objects':len(assets),'unknown_objects':unknown_objects,'measure':'length_m' if kind=='road' else 'area_m2',
            'total':total,'covered':covered,'coverage_fraction':covered/total if total else None,
            'missing_by_reason':dict(categories),'classification':'representative_point_of_missing_cell_intersection_not_field_truth',
            'geometry_association_gaps':registry.audit['entrances_without_building']}
