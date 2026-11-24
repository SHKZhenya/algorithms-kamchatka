"""Demonstration view across all recent non-demo camera frames."""
import time

MAX_AGE = 7200


def aggregate(cameras, now=None, events=()):
    now = time.time() if now is None else now
    eligible, excluded, local, representatives = [], [], [], []
    for camera in cameras:
        observation = camera.get('observation') or {}
        timestamp = observation.get('frame_timestamp')
        version = observation.get('frame_version')
        reason = 'demo' if camera['type'] == 'demo' else (
            'unavailable_or_stale' if not version or timestamp is None or now - timestamp > MAX_AGE else None)
        if reason:
            excluded.append(dict(id=camera['id'], name=camera.get('name'), reason=reason,
                                 reason_label='Демо' if reason == 'demo' else 'Нет кадра за последние 2 часа',
                                 frame_version=version, analysis_at=observation.get('last_analysis_at')))
            continue
        eligible.append(camera)
        scene = observation.get('scene') or {}
        model_label = scene.get('falling_snow') if observation.get('scene_frame_version') == version and scene.get('format_valid') else None
        manual = observation.get('feedback') or {}
        label = manual.get('falling_snow', model_label)
        score = observation.get('snow_probability')
        positive = label == 'yes' or (label not in ('no', 'uncertain') and score is not None and score >= .5)
        negative = label == 'no' or (label not in ('yes', 'uncertain') and score is not None and score <= .1)
        item = dict(camera_id=camera['id'], name=camera.get('name'), location=camera.get('location'),
                    positive=positive, negative=negative, conflict=False, stage=None, timestamp=timestamp,
                    method='operator' if manual else observation.get('analysis_method'), model_only=True,
                    frame_version=version, frame_timestamp=timestamp)
        local.append(item)
        if score is not None:
            representatives.append(dict(id=camera['id'], name=camera.get('name'), location=camera.get('location'),
                                        score=score, timestamp=timestamp, frame_timestamp=timestamp,
                                        frame_version=version, method=observation.get('analysis_method'),
                                        positive=positive, age_seconds=max(0, now-timestamp), weight=1))
    scored = len(representatives)
    for point in representatives:
        point['contribution'] = point['score'] / scored
        point['reason'] = 'Демонстрационный вклад кадра'
    positives = [x for x in local if x['positive']]
    negatives = [x for x in local if x['negative']]
    unknown = [x for x in local if not x['positive'] and not x['negative']]
    state = 'local_positive' if positives else 'inconclusive' if unknown else 'not_detected' if negatives else 'unknown'
    score = sum(x['score'] for x in representatives) / scored if scored else None
    return dict(city='Петропавловск-Камчатский', evidence_scope='demo_all_cameras', snow_score=score,
                local_observation_score=score, status='available' if local else 'insufficient_coverage',
                eligible_cameras=len(eligible), independent_zones=0, minimum_zones=0, calibrated=False,
                excluded={reason: sum(x['reason'] == reason for x in excluded) for reason in ('demo', 'unavailable_or_stale')},
                reason='Демонстрация: все камеры с кадром не старше двух часов, в том числе вне города.',
                method='demo_all_cameras', snowfall_state=state, calculated_at=now,
                last_evidence_at=max((x['timestamp'] for x in local), default=None),
                evidence_count=len(local), negative_evidence_count=len(negatives), inconclusive_evidence_count=len(unknown),
                local_evidence=local, signal_policy='Демонстрационный режим: демо и кадры старше двух часов исключены. Камеры могут быть вне города; оценка не подтверждает обстановку в городе.',
                last_known=[], representatives=representatives,
                contributors=sorted((p for p in representatives if p['positive']), key=lambda p: -p['contribution'])[:3],
                excluded_cameras=excluded, total_weight=scored)
