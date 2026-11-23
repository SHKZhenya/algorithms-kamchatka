"""Spatially conservative aggregation and auditable camera contributions."""
import math
import time

from snowvision.domain.vision import SNAPSHOT_METHOD

CITY = {'name': 'Петропавловск-Камчатский', 'latitude': 53.0533533, 'longitude': 158.6449414,
        'location_note': 'Опорная точка в районе Чехова, 1А; прогноз модельной ячейки, не всей городской территории'}
MAX_EVIDENCE_AGE = 120
EXCLUSION_LABELS = {
    'demo': 'Демонстрационный источник',
    'outside_city_or_unverified_view': 'Городское поле зрения не подтверждено',
    'snapshot_only': 'Одиночный JPEG не входит в городской индекс',
    'unavailable_or_stale': 'Источник недоступен или устарел',
    'insufficient_evidence': 'Недостаточно пригодных признаков или уверенности',
    'expired_analysis': 'Истёк срок анализа',
    'not_in_index_method': 'Метод не входит в формулу городского индекса',
}


def distance_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    value = math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371*2*math.asin(min(1, math.sqrt(value)))


def _evidence(camera, now):
    """Return the current signal evidence using the journal's existing policy."""
    observation = camera.get('observation') or {}
    score = observation.get('snow_probability')
    scene = observation.get('scene') or {}
    version = observation.get('frame_version')
    scene_valid = (scene.get('format_valid') is True and scene.get('air_view') in ('good', 'limited')
                   and observation.get('scene_frame_version') == version
                   and scene.get('falling_snow') in ('yes', 'no'))
    cv_valid = (observation.get('analysis_method') != SNAPSHOT_METHOD
                and score is not None and observation.get('confidence', 0) >= .25)
    timestamp = observation.get('last_analysis_at')
    fresh = (bool(version) and observation.get('source_status') == 'online'
             and not observation.get('is_stale') and timestamp is not None
             and now - timestamp <= MAX_EVIDENCE_AGE and (cv_valid or scene_valid))
    if not fresh:
        return dict(fresh=False, positive=False, negative=False, conflict=False,
                    method=observation.get('analysis_method'), timestamp=timestamp)

    features = observation.get('features') or {}
    weak_motion = features.get('temporal_persistence', 0) >= .6 and features.get('spatial_coverage', 0) >= .4
    cv_yes = cv_valid and (score >= .5 or (score >= .2 and weak_motion))
    scene_yes = scene_valid and scene['falling_snow'] == 'yes'
    conflict = scene_valid and cv_valid and ((scene_yes and score <= .1) or (not scene_yes and score >= .5))
    positive = cv_yes or scene_yes
    negative = not positive and ((cv_valid and score <= .1) or (scene_valid and not scene_yes))
    return dict(fresh=True, positive=positive, negative=negative, conflict=conflict,
                method='weather_vlm_v2' if scene_valid else observation.get('analysis_method'),
                timestamp=timestamp, model_only=scene_valid and not cv_valid,
                score=score if cv_valid else None)


def aggregate(cameras, now=None, events=()):
    now = time.time() if now is None else now
    events_by_camera = {}
    for event in events or ():
        if event.get('is_demo') or event.get('ended_at'):
            continue
        previous = events_by_camera.get(event.get('camera_id'))
        if previous is None or event.get('last_seen', 0) > previous.get('last_seen', 0):
            events_by_camera[event.get('camera_id')] = event

    groups = []
    eligible = 0
    excluded = {}
    excluded_cameras = []
    local_evidence = []
    for camera in cameras:
        observation = camera.get('observation') or {}
        evidence = _evidence(camera, now)
        if camera['type'] != 'demo' and camera.get('city_observation') and evidence['fresh']:
            event = events_by_camera.get(camera['id']) or {}
            local_evidence.append(dict(
                camera_id=camera['id'], name=camera.get('name'), location=camera.get('location'),
                positive=evidence['positive'], negative=evidence['negative'], conflict=evidence['conflict'],
                stage=event.get('stage') if not event.get('evidence_stale') else None,
                timestamp=evidence['timestamp'], method=evidence['method'],
                model_only=evidence.get('model_only', False), frame_version=observation.get('frame_version'),
            ))

        location = camera.get('coordinates_lat_lon')
        reason = None
        if camera['type'] == 'demo': reason = 'demo'
        elif not camera.get('city_observation') or not location: reason = 'outside_city_or_unverified_view'
        elif not camera.get('motion_analysis', True) or observation.get('analysis_method') == SNAPSHOT_METHOD: reason = 'snapshot_only'
        elif observation.get('source_status') != 'online' or observation.get('is_stale'): reason = 'unavailable_or_stale'
        elif observation.get('snow_probability') is None or observation.get('confidence', 0) < 0.25: reason = 'insufficient_evidence'
        elif observation.get('last_analysis_at') is None or now-observation['last_analysis_at'] > MAX_EVIDENCE_AGE: reason = 'expired_analysis'
        elif not evidence['fresh'] or evidence.get('score') is None: reason = 'not_in_index_method'
        if reason:
            excluded[reason] = excluded.get(reason, 0)+1
            excluded_cameras.append(dict(
                id=camera['id'], name=camera.get('name'), reason=reason,
                reason_label=EXCLUSION_LABELS[reason], frame_version=observation.get('frame_version'),
                analysis_at=observation.get('last_analysis_at'),
            ))
            continue

        eligible += 1
        age = max(0, now-observation['last_analysis_at'])
        weight = observation['confidence']*math.exp(-age/MAX_EVIDENCE_AGE)
        point = dict(
            id=camera['id'], name=camera.get('name'), location=camera.get('location'),
            position=location, score=observation['snow_probability'], weight=weight,
            confidence=observation['confidence'], timestamp=observation['last_analysis_at'],
            frame_timestamp=observation.get('frame_timestamp'), frame_version=observation.get('frame_version'),
            method=observation.get('analysis_method'), positive=evidence['positive'],
            conflict=evidence['conflict'],
        )
        neighbors = [group for group in groups if any(distance_km(member['position'], location) < 1 for member in group)]
        if not neighbors:
            groups.append([point])
        else:
            target = neighbors[0]
            target.append(point)
            for neighbor in neighbors[1:]:
                target.extend(neighbor)
                groups.remove(neighbor)

    representatives = [max(group, key=lambda p: (p['weight'], p['timestamp'], p['id'])) for group in groups]
    representatives.sort(key=lambda p: p['id'])
    for group in groups:
        representative = max(group, key=lambda p: (p['weight'], p['timestamp'], p['id']))
        members = sorted(point['id'] for point in group)
        representative['zone_id'] = 'zone:' + members[0]
        representative['zone_members'] = members
    total_weight = sum(p['weight'] for p in representatives)
    local_score = (sum(p['score']*p['weight'] for p in representatives)/total_weight) if representatives else None
    enough = len(groups) >= 2
    for point in representatives:
        point['contribution'] = (point['score']*point['weight']/total_weight) if total_weight else None
        point['age_seconds'] = max(0, now-point['timestamp'])
        point['reason'] = 'Свежее свидетельство из отдельной зоны' if point['score'] > 0 else 'Представитель зоны учтён в знаменателе индекса'
    contributors = sorted(
        (p for p in representatives if p['positive'] and p['score'] > 0 and p['contribution'] is not None),
        key=lambda p: (-p['contribution'], -p['timestamp'], p['id']),
    )[:3]
    last_known = sorted(
        (dict(camera_id=event['camera_id'], name=event.get('name'), location=event.get('location'),
              stage=event.get('stage'), timestamp=event.get('last_seen'))
         for event in events_by_camera.values() if event.get('evidence_stale')),
        key=lambda item: (-(item['timestamp'] or 0), item['camera_id']),
    )
    positive = [item for item in local_evidence if item['positive']]
    conflicts = [item for item in local_evidence if item['conflict']]
    has_fresh = bool(local_evidence)
    if conflicts:
        snowfall_state = 'conflict'
    elif positive:
        confirmed = [item for item in positive if item['stage'] == 'confirmed']
        if confirmed and len({item['camera_id'] for item in confirmed}) >= 2:
            snowfall_state = 'confirmed'
        else:
            snowfall_state = 'local_positive'
    elif has_fresh:
        snowfall_state = 'not_detected'
    else:
        snowfall_state = 'unknown'

    latest = max((item['timestamp'] for item in local_evidence if item['timestamp'] is not None), default=None)
    return dict(
        city=CITY['name'], snow_score=local_score if enough else None,
        local_observation_score=local_score, status='available' if enough else 'insufficient_coverage',
        eligible_cameras=eligible, independent_zones=len(groups), minimum_zones=2,
        calibrated=False, excluded=excluded,
        reason='Минимум две независимые зоны наблюдения. Полнота покрытия города не подтверждена.',
        method='confidence_freshness_weighted_spatial_clusters',
        snowfall_state=snowfall_state, calculated_at=now, last_evidence_at=latest,
        evidence_count=len(local_evidence), local_evidence=local_evidence,
        last_known=last_known,
        representatives=representatives, contributors=contributors,
        excluded_cameras=sorted(excluded_cameras, key=lambda item: item['id']),
        total_weight=total_weight,
    )
