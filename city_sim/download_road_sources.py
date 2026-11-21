"""Download public SKDF records and Overture ranges; no credentials or private API.

Research-only dependencies: requests, pyarrow. Cached responses remain in --output.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import requests

HOST='https://xn--d1aluo.xn--p1ai'


def fetch_json(path,url,payload=None,headers=None):
    if path.exists():return json.loads(path.read_text())
    response=requests.get(url,timeout=45) if payload is None else requests.post(url,json=payload,headers=headers,timeout=45)
    response.raise_for_status();data=response.json();path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False));return data


def skdf(output):
    base=HOST+'/api/v3/portal/hwm/passports/'
    registry=fetch_json(output/'skdf-owner-registry.json',base+'roads/registry?limit=240&offset=0',{'region':[21210],'owner':[2794864]})
    if len(registry['data'])!=registry['total']:raise ValueError('Municipal registry pagination incomplete')
    rows=registry['data']
    region_file=output/'skdf-region-all.json'
    if not region_file.exists():
        first=fetch_json(output/'skdf-region-page-0.json',base+'roads/registry?limit=240&offset=0',{'region':[21210]})
        regional=list(first['data']);step=first['limit']
        for offset in range(step,first['total'],step):
            page=fetch_json(output/f'skdf-region-page-{offset}.json',base+f'roads/registry?limit={step}&offset={offset}',{'region':[21210]})
            regional.extend(page['data'])
        if len(regional)!=first['total'] or len({r['id'] for r in regional})!=len(regional):
            raise ValueError('Regional registry pagination incomplete or changed during download')
        region_file.write_text(json.dumps(dict(total=first['total'],data=regional),ensure_ascii=False))
    rows=rows+[r for r in json.loads(region_file.read_text())['data']
               if 'Петропавловск' in r['full_name'] and r['id'] not in {e['id'] for e in rows}]
    summary=[]
    def one(row):
        passport=row['passport_id'];rid=row['id'];record={'registry':row,'blocks':{},'errors':[]}
        try:
            record['card']=fetch_json(output/'skdf-cards'/f'{passport}.json',base+f'roads/{passport}')
            record['geometry']=fetch_json(output/'skdf-geometries'/f'{rid}.json',HOST+'/api-pg/rpc/f_get_object_geom',
                {'object_id':rid,'object_type':4},{'Content-Profile':'gis_api_public'})
            for child in row.get('children',[]):
                cp=child['passport_id']
                for name in ('roadway','lanes','pavement','km-posts','sidewalks','barrier-fences','clearances','gabarit','median-separations'):
                    if not record['card'].get('blocks',{}).get(name):continue
                    try:
                        page=fetch_json(output/'skdf-blocks'/f'{cp}-{name}.json',base+f'parts/{cp}/{name}?limit=240&offset=0')
                        allrows=list(page.get('data') or [])
                        for offset in range(page.get('limit',240),page.get('total',0),page.get('limit',240)):
                            more=fetch_json(output/'skdf-blocks'/f'{cp}-{name}-{offset}.json',base+f'parts/{cp}/{name}?limit=240&offset={offset}')
                            allrows.extend(more.get('data') or [])
                        record['blocks'][f'{cp}:{name}']={'part':child,'records':allrows}
                    except Exception as error:record['errors'].append(f'{cp}:{name}: {error}')
        except Exception as error:record['errors'].append(str(error))
        return record
    with ThreadPoolExecutor(max_workers=5) as pool:
        for nr,record in enumerate(pool.map(one,rows)):
            summary.append(record)
            if nr%25==0:print('SKDF records',nr+1,'/',len(rows),flush=True)
    (output/'skdf-city-records.json').write_text(json.dumps(summary,ensure_ascii=False))
    print('Saved SKDF records',len(summary),'errors',sum(bool(r['errors']) for r in summary),flush=True)


class HTTPRange(io.RawIOBase):
    def __init__(self,url,size):self.url=url;self.size=size;self.pos=0;self.session=requests.Session();self.bytes_read=0;self.requests=0
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.pos
    def seek(self,offset,whence=0):
        self.pos=offset if whence==0 else self.pos+offset if whence==1 else self.size+offset
        return self.pos
    def read(self,n=-1):
        n=self.size-self.pos if n<0 else min(n,self.size-self.pos)
        if n<=0:return b''
        response=self.session.get(self.url,headers={'Range':f'bytes={self.pos}-{self.pos+n-1}'},timeout=60)
        response.raise_for_status()
        if response.status_code!=206 or len(response.content)!=n:raise ValueError('HTTP Range response mismatch')
        self.pos+=n;self.bytes_read+=n;self.requests+=1;return response.content
    def readinto(self,b):
        data=self.read(len(b));b[:len(data)]=data;return len(data)


def overture(output):
    import pyarrow.parquet as pq
    import pyarrow.compute as pc
    from shapely import from_wkb
    from shapely.geometry import mapping,box
    release='2026-11-18.0';index=output/'overture-collections.parquet'
    if not index.exists():
        r=requests.get(f'https://stac.overturemaps.org/{release}/collections.parquet',timeout=60);r.raise_for_status();index.write_bytes(r.content)
    w,s,e,n=158.5219917,52.8850294,158.9875423,53.1263189
    files=[row for row in pq.read_table(index).to_pylist() if 'type=segment' in row['assets']['aws']['href']
        and row['bbox']['xmin']<e and row['bbox']['xmax']>w and row['bbox']['ymin']<n and row['bbox']['ymax']>s]
    features=[];logs=[]
    for spec in files:
        remote=HTTPRange(spec['assets']['aws']['href'],spec['assets']['aws']['file:size']);parquet=pq.ParquetFile(remote)
        cols={parquet.schema.column(i).path:i for i in range(len(parquet.schema))};groups=[]
        for i in range(parquet.num_row_groups):
            rg=parquet.metadata.row_group(i)
            def stat(name):return rg.column(cols[name]).statistics
            if stat('bbox.xmin').min<e and stat('bbox.xmax').max>w and stat('bbox.ymin').min<n and stat('bbox.ymax').max>s:groups.append(i)
        table=parquet.read_row_groups(groups,columns=['id','geometry','names.primary','sources','width_rules','road_surface',
            'access_restrictions','class','subtype','subclass','road_flags','connectors','bbox'],use_threads=False)
        b=table.column('bbox');mask=pc.and_(pc.and_(pc.less(pc.struct_field(b,'xmin'),e),pc.greater(pc.struct_field(b,'xmax'),w)),
            pc.and_(pc.less(pc.struct_field(b,'ymin'),n),pc.greater(pc.struct_field(b,'ymax'),s)))
        table=table.filter(mask);pq.write_table(table,output/f'overture-city-{spec["id"]}.parquet')
        for row in table.to_pylist():
            g=from_wkb(row.pop('geometry'))
            if g.intersects(box(w,s,e,n)):features.append(dict(type='Feature',id=row['id'],geometry=mapping(g),properties=row))
        logs.append(dict(url=remote.url,file_bytes=remote.size,downloaded_bytes=remote.bytes_read,http_requests=remote.requests,selected_row_groups=groups))
    (output/'overture-city-segments.geojson').write_text(json.dumps(dict(type='FeatureCollection',features=features),ensure_ascii=False,default=str))
    (output/'overture-city-download.json').write_text(json.dumps(dict(release=release,bbox=[w,s,e,n],records=len(features),files=logs,license='ODbL-1.0'),indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',choices=['skdf','overture'],required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    (skdf if a.source=='skdf' else overture)(a.output)
