"""CPU image embeddings and a small, independently trained snow classifier."""

import hashlib
import time

import cv2
import numpy as np

BACKBONE_SHA256 = '047dcff4addef86ea5bc2eff13c9614dc11f47ab1160d0a71a25e7db994f4e1f'


def normalized(values):
    return values / np.maximum(np.linalg.norm(values, axis=-1, keepdims=True), 1e-12)


class LocalWeather:
    """Adapt an ImageNet backbone to labelled clips using two class centroids."""

    def __init__(self, weights):
        with weights.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != BACKBONE_SHA256:
                raise ValueError('Unexpected MobileNetV3 Small weights SHA-256')

        import torch
        from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

        self.torch = torch
        torch.set_num_threads(4)
        self.processor = MobileNet_V3_Small_Weights.IMAGENET1K_V1.transforms()
        model = mobilenet_v3_small(weights=None)
        model.load_state_dict(torch.load(weights, map_location='cpu', weights_only=True))
        self.features = model.features.eval()

    def embedding(self, frames):
        rgb = np.stack([cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) for frame in frames])
        batch = self.processor(self.torch.from_numpy(rgb).permute(0, 3, 1, 2))
        with self.torch.inference_mode():
            features = self.features(batch).mean(dim=(-2, -1)).numpy()
        return normalized(normalized(features).mean(axis=0))


def fit_centroids(examples):
    """Fit from one descriptor per labelled clip, without weighting by clip length."""
    groups = [[vector for snow, vector in examples if snow is label] for label in (False, True)]
    if any(not group for group in groups):
        raise ValueError('Image training requires both snow and non-snow clips')
    return normalized(np.asarray([np.mean(group, axis=0) for group in groups]))


def transient_images(frames):
    """Represent bright transients while removing static structures and broad motion."""
    gray = np.array([cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) for frame in frames])
    background = np.median(gray, axis=0).astype(np.uint8)
    footprint = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    flashes = []
    for frame in gray:
        moving = (cv2.absdiff(frame, background) >= 16).astype(np.uint8)
        broad_motion = cv2.morphologyEx(moving, cv2.MORPH_OPEN, footprint)
        broad_motion = cv2.dilate(broad_motion, footprint)
        positive = cv2.subtract(frame, background)
        positive[(moving == 0) | (broad_motion != 0)] = 0
        flashes.append(positive)
    return [
        cv2.cvtColor(np.max(part, axis=0), cv2.COLOR_GRAY2BGR)
        for part in np.array_split(np.asarray(flashes), 3)
    ]


def sample_images(frames):
    return [frames[i] for i in (0, len(frames) // 2, len(frames) - 1)]


def classify(model, centroids, images, *, weather_centroids=None):
    started = time.monotonic()
    vector = model.embedding(images)
    other, snow = centroids @ vector
    margin = float(snow - other)
    result = {
        'status': 'snowing' if margin > 0 else 'clear',
        'similarity_margin': margin,
    }
    if weather_centroids:
        scores = {label: float(centroid @ vector) for label, centroid in weather_centroids.items()}
        result['weather_class'] = max(scores, key=scores.__getitem__)
    return dict(result, elapsed_seconds=round(time.monotonic() - started, 3))
