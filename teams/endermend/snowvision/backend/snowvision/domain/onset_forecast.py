"""Offline comparison of discrete-time onset forecasts."""

import math
from datetime import datetime
from itertools import groupby
from zoneinfo import ZoneInfo

import numpy as np

HORIZONS = (900, 1800, 2700, 3600)
SPLITS = ('train', 'tune', 'calibration', 'test')
KAMCHATKA = ZoneInfo('Asia/Kamchatka')


def onset_targets(window):
    """Return cumulative labels. Unobserved or ambiguous windows are censored."""
    t = window['issued_at']
    start = window.get('onset')
    if start is not None:
        lower, upper = start
        if not t <= lower < upper <= window['observed_until']:
            raise ValueError('Invalid onset interval')
    result = []
    for horizon in HORIZONS:
        end = t + horizon
        if start is not None and upper <= end:
            result.append(1)
        elif window['observed_until'] >= end and (start is None or end <= lower):
            result.append(0)
        else:
            result.append(None)
    return result


def camera_features(samples, zone, issued_at):
    # Limit the influence of overlapping cameras and polling retries to one
    # strongest score per minute in a verified zone.
    scores = {}
    for _, received, _, row in samples:
        if received > issued_at:
            break
        if (row['zone'] != zone or row['view_revision'] == 'unverified'
                or not row['city_observation'] or row['is_demo']
                or row['source_status'] != 'online' or row['is_stale']):
            continue
        timestamp, score = row['frame_timestamp'], row['snow_probability']
        if timestamp is None or score is None or not issued_at - 1800 < timestamp <= received:
            continue
        minute = int(timestamp // 60)
        if minute not in scores or timestamp > scores[minute][0]:
            scores[minute] = (timestamp, score)
    features = []
    for interval in (300, 900, 1800):
        local = sorted((time, score) for time, score in scores.values() if issued_at-time < interval)
        features.extend((
            sum(score for _, score in local) / len(local) if local else 0,
            local[-1][1] - local[0][1] if local else 0,
            min(1, len(local) / (interval / 60)),
        ))
    features.append(min((issued_at - max(x[0] for x in scores.values())) / 1800, 1) if scores else 1)
    return features


def weather_features(releases, issued_at, end, applicable):
    for available, hours in reversed(releases):
        if available > issued_at:
            continue
        if issued_at - available > 1200 or not applicable:
            break
        midpoint = end - 450
        hour = next((h for h in hours if h['timestamp'] <= midpoint < h['timestamp'] + 3600), None)
        if hour is not None:
            kind = hour.get('precipitation')
            probability = hour.get('precipitation_probability')
            temperature = hour.get('temperature_c')
            return [int(kind == 'snow'), int(kind == 'sleet'), int(kind == 'rain'),
                    probability / 100 if probability is not None else 0, int(probability is None),
                    temperature or 0, int(temperature is None), 1]
        break
    return [0, 0, 0, 0, 1, 0, 1, 0]


def validate_splits(windows):
    grouped = {split: [] for split in SPLITS}
    episode_split = {}
    for row in windows:
        split = row['split']
        grouped[split].append(row)
        if episode_split.setdefault(row['episode'], split) != split:
            raise ValueError('Episode is shared between forecast splits')
    if any(not rows for rows in grouped.values()):
        return False
    for earlier, later in zip(SPLITS, SPLITS[1:]):
        if max(r['issued_at'] for r in grouped[earlier]) + 5400 >= min(
            r['issued_at'] for r in grouped[later]
        ):
            raise ValueError('Forecast splits need a chronological gap over 90 minutes')
    return True


def fit_logistic(x, y, regularization):
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-8] = 1
    normalized = (x-mean)/scale
    weights = np.zeros(normalized.shape[1])
    bias = 0.0
    step = 1 / (0.25 * (np.mean(np.sum(normalized ** 2, axis=1)) + 1) + regularization)
    for _ in range(500):
        residual = sigmoid(normalized @ weights + bias) - y
        weights -= step * ((normalized.T @ residual) / len(y) + regularization * weights)
        bias -= step * float(residual.mean())
    return lambda data: ((data-mean)/scale) @ weights + bias


def sigmoid(value):
    return 1 / (1 + np.exp(-np.clip(value, -40, 40)))


def risk_rows(features, cumulative):
    rows, targets = [], []
    for x, outcomes in zip(features, cumulative):
        previous = 0
        for row, outcome in zip(x, outcomes):
            # A positive after an indeterminate interval identifies the
            # cumulative event, but does not identify its 15-minute bin.
            if previous is None or previous == 1:
                break
            if outcome is not None:
                rows.append(row)
                targets.append(outcome)
            previous = outcome
    return np.asarray(rows), np.asarray(targets)


def score_order(labels, scores):
    positive = sum(labels)
    if not positive:
        return None
    hits = seen = 0
    ap = 0.0
    order = sorted(zip(scores, labels), reverse=True)
    for _, group in groupby(order, key=lambda row: row[0]):
        group = list(group)
        gained = sum(label for _, label in group)
        hits += gained
        seen += len(group)
        ap += gained / positive * hits / seen
    return ap


def evaluate(windows, samples, releases):
    if not validate_splits(windows):
        return {'status': 'insufficient_splits', 'eligible_windows': len(windows)}
    features, targets = {}, {}
    for split in SPLITS:
        features[split], targets[split] = [], []
    for window in windows:
        if window.get('clear_at_issue') is not True:
            continue
        t, zone, split = window['issued_at'], window['zone'], window['split']
        local = datetime.fromtimestamp(t, KAMCHATKA)
        cyclical = 2 * math.pi * (local.hour + local.minute / 60) / 24
        season = 2 * math.pi * local.timetuple().tm_yday / 366
        camera = camera_features(samples, zone, t)
        rows = [
            [math.sin(cyclical), math.cos(cyclical), math.sin(season), math.cos(season), h / 3600]
            + camera + weather_features(releases, t, t+h, window.get('yandex_reference_zone') is True)
            for h in HORIZONS
        ]
        features[split].append(rows)
        targets[split].append(onset_targets(window))
    if any(set(risk_rows(features[s], targets[s])[1]) != {0, 1} for s in SPLITS):
        return {'status': 'insufficient_independent_labels', 'windows': {s: len(targets[s]) for s in SPLITS}}

    def cumulative(model, calibrator, split, columns):
        data = np.asarray(features[split])[:, :, columns]
        logits = model(data)
        hazard = sigmoid(calibrator(logits[..., None]) if calibrator else logits)
        return 1 - np.cumprod(1-hazard, axis=1)

    def errors(prediction, split, horizon=None):
        pred, truth = np.asarray(prediction), np.asarray(targets[split], dtype=float)
        if horizon is not None:
            pred, truth = pred[:, horizon], truth[:, horizon]
        valid = np.isfinite(truth)
        return (pred[valid]-truth[valid])**2

    feature_sets = {
        'season_hour': list(range(5)),
        'camera': list(range(15)),
        'yandex': list(range(5)) + list(range(15, 23)),
        'combined': list(range(23)),
    }
    test, tune = {}, {}
    for name, columns in feature_sets.items():
        x, y = risk_rows(np.asarray(features['train'])[:, :, columns], targets['train'])
        models = [fit_logistic(x, y, penalty) for penalty in (.01, .1, 1)]
        model = min(models, key=lambda m: float(errors(cumulative(m, None, 'tune', columns), 'tune').mean()))
        x, y = risk_rows(np.asarray(features['calibration'])[:, :, columns], targets['calibration'])
        calibrator = fit_logistic(model(x)[:, None], y, .01)
        test[name] = cumulative(model, calibrator, 'test', columns)
        tune[name] = cumulative(model, None, 'tune', columns)

    reports = {}
    episodes = np.array([row['episode'] for row in windows
                         if row['split'] == 'test' and row.get('clear_at_issue') is True])
    for index in (0, 1, 3):
        reference = min((k for k in test if k != 'combined'),
                        key=lambda k: float(errors(tune[k], 'tune', index).mean()))
        truth = np.asarray(targets['test'], dtype=float)[:, index]
        valid = np.isfinite(truth)
        truth, pred = truth[valid], test['combined'][valid, index]
        combined_errors = errors(test['combined'], 'test', index)
        reference_errors = errors(test[reference], 'test', index)
        if not len(truth) or not float(reference_errors.mean()):
            reports[HORIZONS[index] // 60] = {'status': 'insufficient_outcomes'}
            continue
        groups = [np.flatnonzero(episodes[valid] == episode) for episode in sorted(set(episodes[valid]))]
        interval = None
        if len(groups) >= 10 and set(truth) == {0, 1}:
            rng = np.random.default_rng(42)
            estimates = []
            for sample in rng.integers(len(groups), size=(1000, len(groups))):
                selection = np.concatenate([groups[k] for k in sample])
                reference_error = float(reference_errors[selection].mean())
                if reference_error:
                    estimates.append(1 - float(combined_errors[selection].mean()) / reference_error)
            if len(estimates) >= 950:
                interval = np.quantile(estimates, [.025, .975]).tolist()
        bins = np.minimum((pred * 5).astype(int), 4)
        reports[HORIZONS[index] // 60] = {
            'n': len(truth), 'episodes': len(groups), 'unknown': int((~valid).sum()),
            'events': int(truth.sum()), 'reference': reference,
            'brier': {name: float(errors(p, 'test', index).mean()) for name, p in test.items()},
            'brier_skill': 1 - float(combined_errors.mean() / reference_errors.mean()),
            'brier_skill_ci95': interval, 'average_precision': score_order(truth.tolist(), pred.tolist()),
            'passes_skill_gate': interval is not None and interval[0] > 0,
            'camera_feature_coverage': float(np.mean(np.asarray(features['test'])[valid, index, 13] > 0)),
            'weather_feature_coverage': float(np.mean(np.asarray(features['test'])[valid, index, 22] > 0)),
            'reliability': [
                {'n': int((bins == i).sum()), 'predicted': float(pred[bins == i].mean()),
                 'observed': float(truth[bins == i].mean())} for i in range(5) if np.any(bins == i)
            ],
        }
    return {'status': 'offline_evaluation', 'horizons_minutes': reports}
