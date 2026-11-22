"""Spatially conservative aggregation of city camera observations."""
import math
import time
from vision import SNAPSHOT_METHOD

CITY = {'name': 'Петропавловск-Камчатский', 'latitude': 53.0533533, 'longitude': 158.6449414,
        'location_note': 'Опорная точка в районе Чехова, 1А; прогноз модельной ячейки, не всей городской территории'}


def distance_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    value = math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371*2*math.asin(min(1, math.sqrt(value)))


def aggregate(cameras, now=None):
    now = time.time() if now is None else now
    groups = []
    eligible = 0
    excluded = {}
    for camera in cameras:
        o = camera.get('observation') or {}
        location = camera.get('coordinates_lat_lon')
        reason = None
        if camera['type'] == 'demo': reason = 'demo'
        elif not camera.get('city_observation') or not location: reason = 'outside_city_or_unverified_view'
        elif not camera.get('motion_analysis', True) or o.get('analysis_method') == SNAPSHOT_METHOD: reason = 'snapshot_only'
        elif o.get('source_status') != 'online' or o.get('is_stale'): reason = 'unavailable_or_stale'
        elif o.get('snow_probability') is None or o.get('confidence', 0) < 0.25: reason = 'insufficient_evidence'
        elif now-(o.get('last_analysis_at') or 0) > 120: reason = 'expired_analysis'
        if reason:
            excluded[reason] = excluded.get(reason, 0)+1
            continue
        eligible += 1
        weight = o['confidence']*math.exp(-(now-o['last_analysis_at'])/120)
        point = dict(id=camera['id'], position=location, score=o['snow_probability'], weight=weight)
        neighbors = [group for group in groups if any(distance_km(member['position'], location) < 1 for member in group)]
        if not neighbors:
            groups.append([point])
        else:
            target = neighbors[0]
            target.append(point)
            for neighbor in neighbors[1:]:
                target.extend(neighbor)
                groups.remove(neighbor)
    representatives = [max(group, key=lambda p:p['weight']) for group in groups]
    local_score = (sum(p['score']*p['weight'] for p in representatives)/sum(p['weight'] for p in representatives)) if representatives else None
    enough = len(groups) >= 2
    return dict(city=CITY['name'], snow_score=local_score if enough else None,
                local_observation_score=local_score, status='available' if enough else 'insufficient_coverage',
                eligible_cameras=eligible, independent_zones=len(groups), minimum_zones=2,
                calibrated=False, excluded=excluded,
                reason='Минимум две независимые зоны наблюдения. Полнота покрытия города не подтверждена.',
                method='confidence_freshness_weighted_spatial_clusters')
