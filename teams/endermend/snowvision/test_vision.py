import unittest
import cv2
import numpy as np
from app import demo
from vision import analyze_frames, analyze_snapshot

class VisionTests(unittest.TestCase):
    def test_snapshot_scores_visible_flakes_and_clear_frame(self):
        clear = demo(0, 'clear')
        cv2.rectangle(clear, (0, 315), (639, 359), (220, 220, 220), -1)
        snow = clear.copy()
        for y in range(40, 305, 40):
            for x in range(56, 580, 56):
                cv2.circle(snow, (x, y), 2, (235, 235, 235), -1)
        clear_result, snow_result = analyze_snapshot(clear), analyze_snapshot(snow)
        self.assertEqual(clear_result['status'], 'clear')
        self.assertGreater(clear_result['confidence'], .55)
        self.assertEqual(snow_result['status'], 'snowing')
        self.assertGreater(snow_result['confidence'], .60)

    def test_snapshot_with_no_visibility_has_no_score(self):
        result = analyze_snapshot(np.zeros((360, 640, 3), np.uint8))
        self.assertIsNone(result['snow_probability'])
        self.assertEqual(result['confidence'], 0)
        self.assertEqual(result['analysis_reason'], 'poor_visibility')

    def test_static_snow_on_ground(self):
        frame=demo(0,'clear')
        cv2.rectangle(frame,(0,310),(639,359),(240,240,240),-1)
        result=analyze_frames([frame.copy() for _ in range(16)])
        self.assertEqual(result['snow_probability'],0)
    def test_bad_visibility(self):
        result=analyze_frames([np.zeros((360,640,3),np.uint8) for _ in range(8)])
        self.assertIsNone(result['snow_probability'])
        self.assertEqual(result['reason'],'poor_visibility')
    def test_lighting_change(self):
        frames=[demo(0,'clear') for _ in range(8)]
        frames[-1]=cv2.add(frames[-1],np.full_like(frames[-1],60))
        self.assertIsNone(analyze_frames(frames)['snow_probability'])
    def test_camera_shift(self):
        frame=demo(0,'clear')
        frames=[frame.copy() for _ in range(8)]
        frames[-1]=np.roll(frame,12,axis=1)
        self.assertIsNone(analyze_frames(frames)['snow_probability'])

    def test_thin_rain_streaks_are_not_snow(self):
        frames=[]
        rng=np.random.default_rng(12)
        positions=rng.integers([0,0],[620,340],(70,2))
        for tick in range(16):
            frame=demo(0,'clear')
            for x,y in positions:
                point=(int(x),int((y+tick*10)%340))
                cv2.line(frame,point,(point[0]+1,point[1]+16),(220,220,220),1)
            frames.append(frame)
        result=analyze_frames(frames)
        self.assertIsNotNone(result['snow_probability'])
        self.assertLess(result['snow_probability'],.5)
