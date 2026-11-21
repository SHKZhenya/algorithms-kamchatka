import tempfile
import unittest
from unittest.mock import patch
import httpx
from alerts import AlertJournal
from delivery import deliver_once

class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = self.tmp.name + '/db.sqlite'
        self.journal = AlertJournal(self.database)
        self.camera = dict(id='street', type='jpeg', name='Улица')

    def sample(self, version):
        self.journal.observe(self.camera, dict(frame_version=version, source_status='online',
            snow_probability=.8, confidence=.7))

    async def test_durable_early_and_confirmation_delivery_are_distinct_and_idempotent(self):
        self.sample('a')
        journal = AlertJournal(self.database)
        requests = []
        async def receive(request):
            requests.append(request)
            return httpx.Response(204)
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
            await deliver_once(journal, client, 'https://receiver.test', 'test-token')
            self.sample('b'); self.sample('c')
            for _ in range(2):
                await deliver_once(journal, client, 'https://receiver.test', 'test-token')
        self.assertEqual(len(requests), 2)
        self.assertNotEqual(requests[0].headers['Idempotency-Key'], requests[1].headers['Idempotency-Key'])
        self.assertEqual(requests[0].headers['Authorization'], 'Bearer test-token')
        self.assertEqual([d['status'] for d in journal.events()[0]['delivery']], ['delivered','delivered'])

    async def test_failure_retries_after_backoff_and_preserves_key(self):
        self.sample('a')
        keys = []
        def receive(request):
            keys.append(request.headers['Idempotency-Key'])
            return httpx.Response(503 if len(keys)==1 else 200)
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
            await deliver_once(self.journal, client, 'https://receiver.test')
            self.assertFalse(await deliver_once(self.journal, client, 'https://receiver.test'))
            with patch('alerts.time.time', return_value=__import__('time').time()+6):
                await deliver_once(self.journal, client, 'https://receiver.test')
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(self.journal.events()[0]['delivery'][0]['status'], 'delivered')

    async def test_demo_acknowledged_and_expired_are_not_sent(self):
        self.camera['type']='demo'; self.sample('demo')
        self.assertIsNone(self.journal.next_delivery())
        self.camera['type']='jpeg'; self.camera['id']='real'; self.sample('a')
        event=next(e for e in self.journal.events() if not e['is_demo'])
        self.journal.acknowledge(event['id'])
        self.assertIsNone(self.journal.next_delivery())
        self.camera['id']='another'; self.sample('b')
        with patch('alerts.time.time', return_value=__import__('time').time()+121):
            self.assertIsNone(self.journal.next_delivery())

    async def test_confirmation_supersedes_undelivered_early_warning(self):
        self.sample('a'); self.sample('b'); self.sample('c')
        job=self.journal.next_delivery()
        self.assertEqual(job['event']['stage'], 'confirmed')
        self.assertEqual(self.journal.events()[0]['delivery'][0]['status'], 'suppressed')
