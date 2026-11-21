import unittest
import cv2
import httpx
import numpy as np
from sources import fetch_frame

class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_jpeg_decode(self):
        encoded = cv2.imencode('.jpg', np.zeros((32, 48, 3), np.uint8))[1].tobytes()
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=encoded))
        async with httpx.AsyncClient(transport=transport) as client:
            frame = await fetch_frame({'type': 'jpeg', 'url': 'https://example.org/frame.jpg'}, client)
        self.assertEqual(frame.shape, (32, 48, 3))

    async def test_corrupt_frame(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b'broken'))
        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaises(ValueError):
                await fetch_frame({'type': 'jpeg', 'url': 'https://example.org/frame.jpg'}, client)

    async def test_unavailable_source(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(503))
        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                await fetch_frame({'type': 'jpeg', 'url': 'https://example.org/frame.jpg'}, client)

    async def test_hls_burst_decodes_real_segments(self):
        import asyncio
        import functools
        import http.server
        import subprocess
        import tempfile
        import threading
        from pathlib import Path
        from app import demo
        from sources import fetch_burst

        # Real FFmpeg demux/decode with a local, finite HLS fixture, no external network.
        with tempfile.TemporaryDirectory() as directory:
            playlist = str(Path(directory) / 'stream.m3u8')
            frames = b''.join(demo(i).tobytes() for i in range(32))
            await asyncio.to_thread(subprocess.run, [
                'ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'rawvideo',
                '-pix_fmt', 'bgr24', '-s', '640x360', '-r', '4', '-i', 'pipe:0',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '4', '-threads', '1',
                '-f', 'hls', '-hls_time', '1', '-hls_list_size', '0', playlist,
            ], input=frames, check=True, capture_output=True, timeout=20)
            class QuietHandler(http.server.SimpleHTTPRequestHandler):
                def log_message(self, *args): pass
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0),
                functools.partial(QuietHandler, directory=directory))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                burst = await fetch_burst({'url': f'http://127.0.0.1:{server.server_port}/stream.m3u8'})
                self.assertEqual(len(burst), 8)
                self.assertTrue(all(frame.shape == (360,640,3) for frame in burst))
                self.assertEqual(len({frame.tobytes() for frame in burst}),8)
            finally:
                await asyncio.to_thread(server.shutdown)
                server.server_close()
                thread.join(timeout=2)
