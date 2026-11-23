"""Compare actual decoded JPEGs across two samples. Never calls a static sample proof of failure."""
import asyncio,hashlib,json,time
from pathlib import Path
import httpx
from snowvision.adapters.sources import fetch_frame

async def main():
    cameras=[c for c in json.loads((Path(__file__).resolve().parents[1] / 'resources' / 'cameras.json').read_text()) if c['type']=='jpeg']
    limit=asyncio.Semaphore(4)
    async def sample(camera,client):
        async with limit:
            started=time.time()
            try:
                frame=await fetch_frame(camera,client)
                return dict(observed_at=time.time(),sha256=hashlib.sha256(frame.tobytes()).hexdigest(),source_timestamp=camera.get('source_timestamp'),elapsed_seconds=time.time()-started)
            except Exception as exc:return dict(observed_at=time.time(),error=type(exc).__name__)
    async with httpx.AsyncClient(follow_redirects=True,timeout=8) as client:
        first=await asyncio.gather(*(sample(c,client) for c in cameras))
        await asyncio.sleep(35)
        second=await asyncio.gather(*(sample(c,client) for c in cameras))
    rows=[dict(camera_id=c['id'],interval_seconds=c.get('interval_seconds',30),first=a,second=b,
               changed=(a['sha256']!=b['sha256']) if 'sha256' in a and 'sha256'in b else None)
          for c,a,b in zip(cameras,first,second)]
    Path('/tmp/camera-audit.local.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    print(json.dumps(dict(total=len(rows),compared=sum(r['changed'] is not None for r in rows),changed=sum(r['changed'] is True for r in rows),unchanged=sum(r['changed'] is False for r in rows),report='/tmp/camera-audit.local.json')))

if __name__=='__main__':asyncio.run(main())
