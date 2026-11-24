"""Durable webhook delivery, independent from open browser tabs.

At-least-once transport: recipients must deduplicate Idempotency-Key.
"""
import asyncio
from urllib.parse import urlsplit

async def deliver_once(journal, client, url, token=""):
    job = await asyncio.to_thread(journal.next_delivery)
    if job is None:
        return False
    error = None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Invalid webhook URL')
        headers = {'Idempotency-Key': job['id']}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        response = await asyncio.wait_for(client.post(url, json=job['event'], headers=headers), 10)
        response.raise_for_status()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Do not persist receiver URLs, credentials or response content.
        error = type(exc).__name__
    await asyncio.to_thread(journal.delivery_result, job['id'], error)
    return True

async def run_delivery(journal, client, url, token=""):
    while True:
        try:
            worked = await deliver_once(journal, client, url, token)
        except Exception:
            worked = False
        await asyncio.sleep(.1 if worked else 1)
