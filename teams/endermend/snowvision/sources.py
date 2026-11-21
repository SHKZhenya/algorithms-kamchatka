"""Bounded frame acquisition for public JPEG and HLS sources."""
import asyncio
from urllib.parse import urlsplit
from email.utils import parsedate_to_datetime
import cv2
import numpy as np

async def fetch_frame(camera, client):
    if camera['type'] == 'jpeg':
        response = await asyncio.wait_for(client.get(camera['url']), timeout=20)
        response.raise_for_status()
        modified = response.headers.get('last-modified')
        try:
            camera['source_timestamp'] = parsedate_to_datetime(modified).timestamp() if modified else None
        except (ValueError, TypeError):
            camera['source_timestamp'] = None
        data = response.content
        if not data.rstrip().endswith(b'\xff\xd9'):
            raise ValueError('Источник вернул неполный JPEG')
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
