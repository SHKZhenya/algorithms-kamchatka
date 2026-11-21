import unittest
import cv2
import numpy as np
from app import demo
from vision import analyze_frames

class VisionTests(unittest.TestCase):
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
