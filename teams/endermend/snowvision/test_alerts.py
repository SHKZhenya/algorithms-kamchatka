import tempfile
import unittest
from alerts import AlertJournal

class AlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal=AlertJournal(self.tmp.name+'/db.sqlite')
        self.camera=dict(id='street',name='Улица',type='jpeg',interval_seconds=30)
    def sample(self,version,score=.8,source='online'):
        self.journal.observe(self.camera,dict(frame_version=version,snow_probability=score,
            confidence=.7,source_status=source,frame_timestamp=100,analysis_method='opencv_motion'))
    def test_first_evidence_is_immediate_and_distinct_frames_confirm(self):
        self.sample('a')
        self.assertEqual(self.journal.events()[0]['stage'],'early')
        self.sample('a');self.sample('a')
        self.assertEqual(self.journal.events()[0]['stage'],'early')
        self.sample('b');self.sample('c')
        self.assertEqual(self.journal.events()[0]['stage'],'confirmed')
        self.assertEqual(len(self.journal.events()),1)
    def test_rotating_repeated_frames_do_not_confirm_or_end(self):
        self.sample('a'); self.sample('b'); self.sample('a')
        self.assertEqual(self.journal.events()[0]['stage'], 'early')
        self.sample('c')
        self.assertEqual(self.journal.events()[0]['stage'], 'confirmed')
        self.sample('clear-a', .1); self.sample('clear-b', .1); self.sample('clear-a', .1)
        self.assertIsNone(self.journal.events()[0]['ended_at'])

    def test_unknown_breaks_confirmation_and_does_not_end_episode(self):
        self.sample('a');self.sample('b');self.sample('x',None,'offline');self.sample('c')
        self.assertEqual(self.journal.events()[0]['stage'],'early')
        self.assertIsNone(self.journal.events()[0]['ended_at'])
    def test_acknowledgement_and_restart_survive(self):
        self.sample('a');id=self.journal.events()[0]['id']
        self.journal.acknowledge(id)
        reloaded=AlertJournal(self.tmp.name+'/db.sqlite')
        self.assertIsNotNone(reloaded.events()[0]['acknowledged_at'])
        self.assertEqual(reloaded.events()[0]['id'],id)
    def test_three_clear_samples_end_episode(self):
        self.sample('a')
        for version in ('b','c','d'): self.sample(version,.1)
        self.assertEqual(self.journal.events()[0]['stage'],'ended')
    def test_demo_is_explicit(self):
        self.camera['type']='demo';self.sample('a')
        self.assertTrue(self.journal.events()[0]['is_demo'])

    def test_weak_spatial_motion_warns_without_confirmation(self):
        for version in ('a','b','c'):
            self.journal.observe(self.camera,dict(frame_version=version,snow_probability=.3,confidence=.7,
                source_status='online',features={'temporal_persistence':.8,'spatial_coverage':.8}))
        self.assertEqual(self.journal.events()[0]['stage'],'early')

    def test_confirmation_survives_unknown_and_subsequent_weak_evidence(self):
        for version in ('a', 'b', 'c'):
            self.sample(version)
        confirmed_at = self.journal.events()[0]['confirmed_at']
        self.sample('offline', None, 'offline')
        self.journal.observe(self.camera, dict(frame_version='weak', snow_probability=.3, confidence=.7,
            source_status='online', features={'temporal_persistence':.8,'spatial_coverage':.8}))
        event = self.journal.events()[0]
        self.assertEqual((event['stage'], event['confirmed_at'], event['confirmations']), ('confirmed', confirmed_at, 0))
    def test_model_evidence_without_fabricated_probability(self):
        for version in ('a','b','c'):
            self.journal.observe(self.camera,dict(frame_version=version,snow_probability=None,confidence=0,
                source_status='online',scene_frame_version=version,
                scene={'format_valid':True,'air_view':'limited','falling_snow':'yes'}))
        event=self.journal.events()[0]
        self.assertEqual(event['stage'],'confirmed')
        self.assertIsNone(event['score'])
        self.assertIsNone(event['confidence'])
    def test_conflicting_methods_require_review(self):
        for version in ('a','b','c'):
            self.journal.observe(self.camera,dict(frame_version=version,snow_probability=0,confidence=.7,
                source_status='online',scene_frame_version=version,
                scene={'format_valid':True,'air_view':'good','falling_snow':'yes'}))
        self.assertEqual(self.journal.events()[0]['stage'],'early')
        self.assertTrue(self.journal.events()[0]['conflict'])
