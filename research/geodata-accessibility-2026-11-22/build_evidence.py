"""Assemble aggregate research evidence; raw SKDF/PDF data stay in local out/."""
import json
from pathlib import Path
from hashlib import sha256,sha1
import shutil

ROOT=Path('out/geodata-accessibility-2026-11-22')
DEST=Path('research/geodata-accessibility-2026-11-22')
fac=json.loads((ROOT/'facades-checked/results.json').read_text())
repeat=json.loads((ROOT/'facades-repeat/results.json').read_text())
for key in ('metrics','comparisons','feature_contrasts','predictions','folds','feature_evidence','terrain_quality'):
    assert fac[key]==repeat[key],key
shutil.copyfile(ROOT/'facades-checked/results.json',DEST/'facade-results.json')
source=json.loads((ROOT/'import-delivery-verified/results.json').read_text())
assert (ROOT/'import-delivery/road-evidence.geojson').read_bytes()==(ROOT/'import-delivery-verified/road-evidence.geojson').read_bytes()
for name,expected in source['source_sha256'].items():
    assert sha256(Path(name).read_bytes()).hexdigest()==expected,name
for key in ('overture','skdf'):
    source[key].pop('audit',None)
source['overture'].pop('raw_width_values',None)
source['raw_audit_path']=str(ROOT/'import-delivery-verified/results.json')
(DEST/'source-results.json').write_text(json.dumps(source,ensure_ascii=False,indent=2))
ledger=[
 dict(source='ФГИС СКДФ',url='https://skdf.ru/',api='https://xn--d1aluo.xn--p1ai/api/v3/portal/',
      status='public_data_downloaded',selected_records=223,municipal_owner_records=199,other_region_or_federal_records=24,
      rights='Public access confirmed; an open redistribution/persistent commercial reuse grant was not verified',
      raw=str(ROOT/'sources/skdf-city-records.json'),new_local_width_ways=source['skdf']['matched']['width_m']['new_osm_ways']),
 dict(source='ПКГО КСОДД',url='https://pkgo.ru/upload/medialibrary/d1a/d1a47f280e3871b728a1a70a7508f5b5.pdf',
      status='downloaded_250_pages',rights='Official municipal publication; local factual extraction, no blanket license asserted',
      local_width_gain=0,source_class='historical_inventory_and_planning',raw=str(ROOT/'sources/ksodd.pdf')),
 dict(source='ПКГО project Spartak',url='https://pkgo.ru/upload/iblock/9ab/9ab0e61a48338f078117c489da699dd2.pdf',
      status='downloaded_154_pages',width_m=7,lanes=2,sidewalk_m=2,source_pdf_page=11,source_class='design',as_built_verified=False,
      horizontal_crs='МСК-41; exact transformation not verified',vertical_datum='Baltic 1977; not EGM2008',
      local_width_gain=0,rights='Official published project; reuse grant not asserted',raw=str(ROOT/'sources/pkgo-spartak-plan.pdf')),
 dict(source='ПКГО gazette 633',url='https://pkgo.ru/upload/iblock/5d1/5d1bfe39f741c0639bf9376d8ef4e722.pdf',
      status='downloaded_96_pages',publication_date='2018-01-19',source_pdf_page=51,source_class='investment_project_design_characteristics',
      design_widths=[dict(street='улица Чубарова',width_range_m=[7,7],lanes=2),dict(street='улица Вулканная',width_range_m=[15,18.5],lanes_range=[4,5])],
      local_width_gain=0,rights='Official municipal publication; no current/as-built inference',raw=str(ROOT/'sources/pkgo-gazette-633.pdf')),
 dict(source='Overture Maps transportation',url='https://docs.overturemaps.org/guides/transportation/',
      release='2026-11-18.0',license='ODbL-1.0',attribution='© OpenStreetMap contributors, Overture Maps Foundation',
      status='city_polygon_intersection_downloaded',segments=source['overture']['road_segments'],width_nonempty=source['overture']['width_rules_nonempty'],
      new_local_width_ways=0,source_class='cartographic; not independent field survey',raw=str(ROOT/'sources/overture-city-segments.geojson')),
 dict(source='Региональный геопортал',urls=['https://map.kamgov.ru/','http://map.kamgov.ru/','https://agate.kamgov.ru/'],
      status='503_or_TLS_error',records_obtained=0,city_coverage=None,new_confirmed_attributes=0,rights='No dataset license obtained; not an absence claim'),
 dict(source='Mapillary',url='https://www.mapillary.com/developer/api-documentation',license='Imagery CC-BY-SA with author attribution; API terms/token separate',
      status='no_token_API_error_190',records_obtained=0,city_coverage=None,new_confirmed_attributes=0,
      raw=str(ROOT/'sources/mapillary-no-token.txt')),
 dict(source='KartaView',url='https://github.com/kartaview/mcp-karta-view/blob/main/src/mcp_karta_view/karta_view_client.py',
      status='official_client_endpoint_TLS_hostname_mismatch',records_obtained=0,city_coverage=None,new_confirmed_attributes=0,
      raw=str(ROOT/'sources/kartaview-fetch.json')),
 dict(source='2GIS Places database_entrances',url='https://docs.2gis.com/api/search/places/reference/3.0/items/byid',
      terms='https://law.2gis.ru/webapi-offer',status='no_key; additional field permission and separate storage/reuse rights required',
      response_meta_code=400,records_obtained=0,entrance_city_coverage=None,new_confirmed_entrances=0,
      rights='Default offer 3.1 disallows extraction/storage/use outside contract; caching not provided',raw=str(ROOT/'sources/2gis-no-key.txt')),
 dict(source='Yandex Maps',url='https://yandex.ru/dev/commercial/doc/ru/',terms='https://yandex.ru/legal/maps_api/ru',
      status='no_key_or_extended_storage_license_supplied',records_obtained=0,city_coverage=None,new_confirmed_attributes=0,
      rights='Free API conditions do not grant persistent geocoder-result database use; extended rights must be checked')]
for item in ledger:
    p=Path(item.get('raw',''))
    if item.get('raw') and p.is_file():item['sha256']=sha256(p.read_bytes()).hexdigest()
(DEST/'source-ledger.json').write_text(json.dumps(dict(checked_on='2026-11-22',sources=ledger),ensure_ascii=False,indent=2))
previous=json.loads(Path('research/mobility-backend-2026-11-22/verification.json').read_text())
assert sha256(Path('city_sim/facade_inference.py').read_bytes()).hexdigest()==previous['source_sha256']['facade_inference.py']
ui=json.loads(Path('research/object-backend-2026-11-22/ui-rollback-verification.json').read_text())
for f in ui['checks']:
    data=Path(f['path']).read_bytes();assert sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==f['expected'],f['path']
paths=['city_sim/facade_access_experiment.py','city_sim/road_evidence.py','city_sim/import_road_sources.py',
       'city_sim/download_road_sources.py','city_sim/mobility_api.py','city_sim/tests/test_road_sources.py']
verify=dict(date='2026-11-22',identical_spatial_predictions=True,identical_coefficients=True,identical_bootstrap=True,identical_source_import=True,
    production_facade_model_unchanged=True,ui_unchanged_files=len(ui['checks']),canonical_graph_modified=False,
    source_sha256={p:sha256(Path(p).read_bytes()).hexdigest() for p in paths},
    external_layer_sha256=source['artifact_sha256'],tests=dict(city_sim=77,core=9,status='passed'),
    data_rights='SKDF raw and API layer remain local; no public publication or deployment performed')
verify['real_data_api_smoke']=json.loads((ROOT/'api-smoke.json').read_text())
assert verify['real_data_api_smoke']['sha256']==verify['external_layer_sha256']
(DEST/'verification.json').write_text(json.dumps(verify,ensure_ascii=False,indent=2))
(DEST/'test-results.txt').write_text(Path('/private/tmp/kam-geodata-complete-tests.log').read_text()+'\nCore:\n'+Path('/private/tmp/kam-geodata-core-tests.log').read_text())
print(json.dumps({'sources':len(ledger),'width_new':source['combined']['width_m']['new_osm_ways'],'model_top1':fac['metrics']['all']['methods']['access_no_terrain_no_neighbours']['top1_hit'],'ui':len(ui['checks'])}))
