"""Explainable motion score for sufficiently frequent camera sequences."""
import cv2
import numpy as np

cv2.setNumThreads(1)


def frame_quality(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return dict(brightness=float(np.mean(gray)), contrast=float(np.std(gray)),
                sharpness=float(cv2.Laplacian(gray, cv2.CV_32F).var()))


def analyze_frames(frames):
    result = dict(snow_probability=None, confidence=0, status='unknown',
                  reason='insufficient_frames', features={})
    if len(frames) < 8:
        return result
    if any(f is None or f.shape != frames[0].shape for f in frames):
        result['reason'] = 'invalid_frames'
        return result
    gray = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    brightness = float(np.mean(gray[-1]))
    contrast = float(np.std(gray[-1]))
    sharpness = float(cv2.Laplacian(gray[-1], cv2.CV_32F).var())
    result['features'] = dict(brightness=brightness, contrast=contrast, sharpness=sharpness)
    if brightness < 6 or contrast < 5 or sharpness < 2:
        result['reason'] = 'poor_visibility'
        return result
    counts, spreads = [], []
    for previous, current in zip(gray, gray[1:]):
        background_previous = cv2.GaussianBlur(cv2.medianBlur(previous, 9), (9, 9), 0).astype(np.float32)
        background_current = cv2.GaussianBlur(cv2.medianBlur(current, 9), (9, 9), 0).astype(np.float32)
        shift, response = cv2.phaseCorrelate(background_previous, background_current)
        points = cv2.goodFeaturesToTrack(background_previous.astype(np.uint8), maxCorners=80, qualityLevel=0.05, minDistance=20)
        if points is not None and len(points) >= 6:
            tracked, valid, _ = cv2.calcOpticalFlowPyrLK(background_previous.astype(np.uint8), background_current.astype(np.uint8), points, None)
            if tracked is not None:
                movements = (tracked-points).reshape(-1, 2)[valid.reshape(-1) == 1]
                if len(movements) >= 6:
                    translation = np.median(movements, axis=0)
                    agreement = np.mean(np.linalg.norm(movements-translation, axis=1) < 2)
                    if response > 0.2 and np.linalg.norm(shift) > 2.5 and np.linalg.norm(translation) > 2.5 and agreement > 0.65:
                        result['reason'] = 'camera_motion'
                        return result
        delta = current.astype(np.float32)-previous.astype(np.float32)
        illumination = float(np.median(delta))
        if abs(illumination) > 35:
            result['reason'] = 'lighting_change'
            return result
        residual = delta-illumination
        noise = float(np.median(np.abs(residual)))
        mask = ((residual > max(25, 4*noise)) & (current > 100)).astype('uint8')
        if np.mean(mask) > 0.25:
            result['reason'] = 'scene_change'
            return result
        _, _, stats, centers = cv2.connectedComponentsWithStats(mask)
        small = [(s, c) for s, c in zip(stats[1:], centers[1:])
                 if 3 <= s[4] <= 60 and 2 <= s[2] <= 14 and 2 <= s[3] <= 16 and s[3] <= 3*s[2]]
        counts.append(len(small))
        cells = {(min(3, int(c[0]*4/current.shape[1])), min(2, int(c[1]*3/current.shape[0]))) for _, c in small}
        spreads.append(len(cells)/12)
    density = float(np.median(counts))
    spread = float(np.median(spreads))
    persistence = float(np.mean(np.array(counts) >= 8))
    score = float(np.clip(density/55 * spread * persistence, 0, 1))
    confidence = float(np.clip(contrast/45, 0, 1)*min(len(frames)/16, 1))
    result.update(snow_probability=score, confidence=confidence,
                  status='snowing' if score >= 0.5 else 'clear', reason=None)
    result['features'].update(particle_count=density, spatial_coverage=spread, temporal_persistence=persistence)
    return result


def analyze(frames):
    result = analyze_frames(frames)
    return result['snow_probability'], result['confidence'], result['status']
