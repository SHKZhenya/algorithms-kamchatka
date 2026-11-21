"""Fetch a small, reproducible OSM entrance supplement; preserve provider timestamps.

python -m city_sim.prepare_access_sources --endpoint https://overpass.kumi.systems/api/interpreter
Nothing is uploaded. A snapshot does not certify current real-world entrance locations.
"""
import argparse
from datetime import datetime,timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import httpx
from .geodata import BOUNDS

QUERY='[out:xml][timeout:25];(node[entrance](52.91,158.48,53.16,158.80);node["building"="entrance"](52.91,158.48,53.16,158.80););out meta;'

def fetch(endpoint,output):
    response=httpx.get(endpoint,params={'data':QUERY},timeout=45,headers={'User-Agent':'KamchatkaResearch/1.0'})
    response.raise_for_status();data=response.content;root=ET.fromstring(data)
    if root.tag!='osm' or root.find('remark') is not None:raise ValueError('Incomplete OSM response')
    output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(gzip.compress(data,mtime=0))
    meta=root.find('meta')
    manifest={'retrieved_at':datetime.now(timezone.utc).isoformat(),'endpoint':endpoint,'query':QUERY,
        'license':'ODbL 1.0','attribution':'OpenStreetMap contributors','osm_base':meta.attrib if meta is not None else None,
        'sha256_xml':sha256(data).hexdigest(),'sha256_gzip':sha256(output.read_bytes()).hexdigest(),
        'nodes':len(root.findall('node')),'merge_policy':'newest_version_per_element_no_inferred_deletions'}
    output.with_suffix('.source.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    return manifest

def refresh_official(output):
    """Verify known IDs at the official API; this cannot discover unknown new IDs."""
    root=ET.parse(gzip.open(output)).getroot()
    nodes=list(root.findall('node')); refreshed=ET.Element('osm',version='0.6',generator='OSM official known-entrance refresh')
    requests=[]
    for offset in range(0,len(nodes),250):
        ids=','.join(n.get('id') for n in nodes[offset:offset+250])
        response=httpx.get('https://api.openstreetmap.org/api/0.6/nodes',params={'nodes':ids},timeout=35)
        response.raise_for_status();batch=ET.fromstring(response.content)
        received=batch.findall('node')
        if len(received)!=len(nodes[offset:offset+250]):
            raise ValueError('Official API did not return every requested node; keep the original snapshot')
        refreshed.extend(received)
        requests.append({'url':str(response.url),'nodes':len(received),'sha256_xml':sha256(response.content).hexdigest()})
    payload=ET.tostring(refreshed,encoding='utf-8',xml_declaration=True)
    output.write_bytes(gzip.compress(payload,mtime=0))
    source=output.with_suffix('.source.json');manifest=json.loads(source.read_text())
    manifest['official_refresh']={'retrieved_at':datetime.now(timezone.utc).isoformat(),'requests':requests}
    manifest['sha256_xml']=sha256(payload).hexdigest();manifest['sha256_gzip']=sha256(output.read_bytes()).hexdigest()
    source.write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--endpoint',required=True)
    p.add_argument('--output',type=Path,default=Path('city_sim/data/access-supplement.osm.gz'))
    p.add_argument('--verify-known-official',action='store_true')
    args=p.parse_args();manifest=fetch(args.endpoint,args.output)
    if args.verify_known_official:manifest=refresh_official(args.output)
    print(json.dumps(manifest,ensure_ascii=False))
