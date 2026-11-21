"""Offline source matching, coverage accounting and opt-in attributed API layer."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from shapely.geometry import LineString,shape,mapping
from shapely.ops import transform
from weather_coverage.geography import read_osm,relation_polygon,TO_METRIC
from .api import make_app
from .object_registry import ObjectRegistry
from .road_evidence import overture_records,skdf_records,skdf_contexts,coverage,RoadEvidence,street_key


def city_boundary():
    path=Path('weather_coverage/osm/city-boundary.osm.gz');elements=read_osm([path])
    pts={k:(float(e.get('lon')),float(e.get('lat'))) for k,e in elements.items() if k.startswith('n')}
    lines={k:LineString([pts['n'+n.get('ref')] for n in e.findall('nd')]) for k,e in elements.items()
           if k.startswith('w') and len(e.findall('nd'))>=2 and all('n'+n.get('ref') in pts for n in e.findall('nd'))}
    return relation_polygon(elements['r4139922'],lines)


def municipal_context(registry):
    streets=['проспект Победы','улица Тушканова','улица Зеркальная','улица Владивостокская','улица Ленинградская','проспект 50 лет Октября']
    return [dict(name=name,lanes=4,surface='asphalt_concrete',source='КСОДД ПКГО, page 32',
        source_url='https://pkgo.ru/upload/medialibrary/d1a/d1a47f280e3871b728a1a70a7508f5b5.pdf',
        kind='official_inventory_historical_street_context',local_scope_verified=False,
        candidate_osm_ways=[f['id'] for f in registry.catalog.roads.features if street_key(f['properties'].get('name'))==street_key(name)],
        width_m=None,limitation='General street description; no chainage or proof of current lane configuration') for name in streets]


def run(sources,output):
    output.mkdir(parents=True,exist_ok=True);app=make_app(db_path=output/'scratch.sqlite');reg=ObjectRegistry(app.state.city)
    boundary=city_boundary();metric_city=transform(TO_METRIC,boundary)
    raw=json.loads((sources/'overture-city-segments.geojson').read_text())['features']
    city_features=[f for f in raw if boundary.intersects(shape(f['geometry']))]
    overture,audit_o=overture_records(city_features,reg,metric_city)
    raw_skdf=json.loads((sources/'skdf-city-records.json').read_text())
    skdf, audit_s=skdf_records(raw_skdf,reg,metric_city)
    contexts=skdf_contexts(raw_skdf,audit_s,reg)
    records=overture+skdf
    layer=dict(type='FeatureCollection',features=records,contexts=contexts,metadata=dict(city_revision=reg.revision,created_on='2026-11-22',
        sources=['Overture transportation 2026-11-18.0','ФГИС СКДФ public portal'],
        attribution='© OpenStreetMap contributors, Overture Maps Foundation; ФГИС СКДФ / РОСДОРНИИ',
        canonical_graph_modified=False,field_measured_records=0,redistribution='SKDF terms not verified; local research only'))
    path=output/'road-evidence.geojson';path.write_text(json.dumps(layer,ensure_ascii=False,allow_nan=False))
    # Validate the exact artifact that the backend will open.
    RoadEvidence(path,reg.revision)
    def count(field):return sum(bool(f['properties'].get(field)) for f in city_features)
    report=dict(date='2026-11-22',city_revision=reg.revision,canonical_osm_ways=len(reg.catalog.roads.features),
        city_intersecting_osm_ways=sum(metric_city.intersects(g) for g in reg.catalog.metric_roads),
        city_clipped_osm_length_m=sum(metric_city.intersection(g).length for g in reg.catalog.metric_roads),
        city_boundary=dict(osm_id='relation/4139922',bounds=list(boundary.bounds),status='conditional_OSM_place_boundary_not_official_survey'),
        overture=dict(downloaded_bbox_records=len(raw),intersecting_city_records=len(city_features),
            road_segments=sum(f['properties'].get('subtype')=='road' for f in city_features),
            width_rules_nonempty=count('width_rules'),surface_nonempty=count('road_surface'),restrictions_nonempty=count('access_restrictions'),
            raw_width_values=[dict(id=f['id'],rules=f['properties']['width_rules'],sources=f['properties'].get('sources')) for f in city_features if f['properties'].get('width_rules')],
            matched=coverage(overture,reg),audit=audit_o),
        skdf=dict(queried_records=len(audit_s),records_with_geometry=sum(bool(o.get('geometry',{}).get('features')) for o in json.loads((sources/'skdf-city-records.json').read_text())),
            records_with_matched_osm=sum(bool(r['matched_osm_ways']) for r in audit_s),
            records_with_width_rows=sum(bool(r['width_rows']) for r in audit_s),width_rows=sum(len(r['width_rows']) for r in audit_s),
            matched=coverage(skdf,reg),audit=audit_s,
            extra_blocks={kind:dict(records=sum(len(b['records']) for obj in raw_skdf for key,b in obj.get('blocks',{}).items() if key.endswith(':'+kind)),
                rows_with_explicit_width=sum(row.get('sidewalk_width') is not None for obj in raw_skdf for key,b in obj.get('blocks',{}).items() if key.endswith(':'+kind) for row in b['records']) if kind=='sidewalks' else None)
                for kind in ('sidewalks','barrier-fences','clearances','gabarit','median-separations')},
            related_context_counts=dict(Counter(c['quantity'] for c in contexts))),
        combined=coverage(records,reg),municipal_context=municipal_context(reg),
        artifact=str(path),artifact_sha256=sha256(path.read_bytes()).hexdigest(),
        source_counts_by_kind=dict(Counter(f['properties']['kind'] for f in records)),
        source_sha256={str(p):sha256(p.read_bytes()).hexdigest() for p in [sources/'overture-city-segments.geojson',sources/'skdf-city-records.json',Path('city_sim/road_evidence.py'),Path(__file__)]})
    (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2))
    (output/'overture-city.geojson').write_text(json.dumps(dict(type='FeatureCollection',features=city_features),ensure_ascii=False))
    print(json.dumps({k:report[k]['matched'] for k in ('overture','skdf')},ensure_ascii=False),flush=True)
    print('combined',report['combined'],flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sources',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.sources,a.output)
