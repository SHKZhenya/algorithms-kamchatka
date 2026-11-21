"""Bounded frame acquisition for public JPEG and HLS sources."""
import asyncio
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from email.utils import parsedate_to_datetime
import cv2
import httpx
import numpy as np

async def fetch_frame(camera, client):
    if camera['type'] == 'jpeg':
        return await asyncio.wait_for(fetch_jpeg(camera, client), 22)
    elif camera['type'] == 'hls':
        process = await asyncio.create_subprocess_exec(
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-rw_timeout', '10000000', *(['-tls_verify', '1'] if urlsplit(camera['url']).scheme == 'https' else []), '-i', camera['url'],
            '-frames:v', '1', '-vf', 'scale=640:360',
            '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            data, error = await asyncio.wait_for(process.communicate(), timeout=25)
            if process.returncode:
                raise ValueError(error.decode(errors='replace')[-500:])
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()
    else:
        raise ValueError('Unsupported camera type')
    if len(data) > 20 * 1024 * 1024:
        raise ValueError('Слишком большой файл изображения')
    frame = await asyncio.to_thread(cv2.imdecode, np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError('Источник вернул повреждённое изображение')
    if min(frame.shape[:2]) < 16 or max(frame.shape[:2]) > 12000:
        raise ValueError('Недопустимый размер кадра')
    return frame

async def fetch_burst(camera):
    """A single HLS connection yields eight ordered frames at 2 FPS."""
    process = await asyncio.create_subprocess_exec(
        'ffmpeg','-hide_banner','-loglevel','error','-rw_timeout','10000000',
        *(['-tls_verify','1'] if urlsplit(camera['url']).scheme == 'https' else []),
        '-live_start_index','-1','-i',camera['url'],'-vf','fps=2,scale=640:360','-frames:v','8',
        '-f','rawvideo','-pix_fmt','bgr24','pipe:1',
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
    )
    try:
        data,error=await asyncio.wait_for(process.communicate(),30)
        if process.returncode:
            raise ValueError(error.decode(errors='replace')[-500:])
        size=640*360*3
        if len(data)!=8*size:
            raise ValueError('Неполная видеосерия')
        return [frame.copy() for frame in np.frombuffer(data,np.uint8).reshape(8,360,640,3)]
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()


async def fetch_jpeg(camera, client):
    """Two bounded attempts. Publish metadata only after a complete decoded JPEG."""
    for attempt in range(2):
        try:
            url = camera['url']
            if camera.get('cache_bust', True):
                parts = urlsplit(url)
                query = [(k,v) for k,v in parse_qsl(parts.query,keep_blank_values=True) if k != '_sv']
                url = urlunsplit(parts._replace(query=urlencode(query + [('_sv', str(time.time_ns()))])))
            async with client.stream('GET', url, headers={
                'Cache-Control':'no-cache, no-store, max-age=0', 'Pragma':'no-cache',
                'Accept':'image/jpeg',
            }, timeout=8) as response:
                response.raise_for_status()
                chunks=[]; size=0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 20*1024*1024: raise ValueError('Слишком большой JPEG')
                    chunks.append(chunk)
                data=b''.join(chunks)
                if not data.startswith(b'\xff\xd8') or not data.rstrip().endswith(b'\xff\xd9'):
                    raise ValueError('Неполный или недопустимый JPEG')
                frame=await asyncio.to_thread(cv2.imdecode,np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR)
                if frame is None or min(frame.shape[:2])<16 or max(frame.shape[:2])>12000:
                    raise ValueError('Недопустимый JPEG-кадр')
                try: source_time=parsedate_to_datetime(response.headers['last-modified']).timestamp()
                except (KeyError,ValueError,TypeError,OverflowError): source_time=None
                previous_time=camera.get('source_timestamp')
                if source_time is not None and previous_time is not None and source_time<previous_time:
                    raise ValueError('Источник вернул более старый снимок')
                camera.update(source_timestamp=source_time, download_attempts=attempt+1,
                              http_cache_age=response.headers.get('age'))
                return frame
        except asyncio.CancelledError: raise
        except httpx.HTTPStatusError as exc:
            if attempt or exc.response.status_code not in (408,500,502,503,504): raise
            await asyncio.sleep(.5)
        except (httpx.HTTPError, ValueError):
            if attempt: raise
            await asyncio.sleep(.5)
