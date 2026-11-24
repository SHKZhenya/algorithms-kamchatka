"""Shadow detector for evaluating air masks and stationary highlights."""

import cv2
import numpy as np

from snowvision.domain.vision import analyze_frames, analyze_snapshot


def unknown(reason):
    return dict(snow_probability=None, status='unknown', confidence=0,
                analysis_reason=reason, features={})


def background(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(cv2.medianBlur(gray, 9), (9, 9), 0).astype(np.float32)


def compensate_jitter(frames):
    anchor = background(frames[-1])
    height, width = anchor.shape
    aligned = []
    for frame in frames[:-1]:
        (dx, dy), response = cv2.phaseCorrelate(anchor, background(frame))
        if response >= .5 and .3 <= np.hypot(dx, dy) <= 2.5:
            matrix = np.float32([[1, 0, -dx], [0, 1, -dy]])
            frame = cv2.warpAffine(frame, matrix, (width, height), borderMode=cv2.BORDER_REFLECT_101)
        aligned.append(frame)
    return [*aligned, frames[-1]]


def detect(frames, method, config, previous=None):
    gray = cv2.cvtColor(frames[-1], cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    air = np.zeros((height, width), dtype=np.uint8)
    polygons = config.get('air')
    if polygons:
        for polygon in polygons:
            points = np.rint(np.asarray(polygon) * [width - 1, height - 1]).astype(np.int32)
            cv2.fillPoly(air, [points], 1)
    else:
        air.fill(1)
    for polygon in config.get('exclude', []):
        points = np.rint(np.asarray(polygon) * [width - 1, height - 1]).astype(np.int32)
        cv2.fillPoly(air, [points], 0)
    region = air.astype(bool)
    if np.mean(region) < .05:
        return unknown('no_air_region')
    scene_pixels = gray[region]
    if np.mean(scene_pixels >= 250) > .35:
        return unknown('air_overexposed')
    if np.mean(scene_pixels <= 8) > .75:
        return unknown('air_underexposed')
    if method == 'opencv_snapshot':
        if previous is not None and previous.shape == gray.shape:
            # Reject static small highlights only when the viewpoint and
            # illumination are sufficiently similar to the previous sample.
            difference = cv2.absdiff(gray, previous)
            if float(np.percentile(difference[region], 90)) <= 18:
                kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
                old_highlights = cv2.morphologyEx(previous, cv2.MORPH_TOPHAT, kernel)
                static = (old_highlights >= 13) & (difference <= 8)
                region &= ~static
                if np.mean(region) < .05:
                    return unknown('no_air_region')
        return analyze_snapshot(frames[-1], eligible_pixels=region)
    result = analyze_frames(compensate_jitter(frames), eligible_pixels=region)
    result['analysis_reason'] = result.pop('reason')
    return result
