"""Spatially conservative camera aggregation and cached hourly weather outlook."""
import asyncio
import json
import math
import time
import httpx
from pathlib import Path

CITY = {'name': 'Петропавловск-Камчатский', 'latitude': 53.0533533, 'longitude': 158.6449414,
        'location_note': 'Опорная точка в районе Чехова, 1А; прогноз модельной ячейки, не всей городской территории'}
SNOW_CODES = {71, 73, 75, 77, 85, 86}


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
        elif not camera.get('motion_analysis', True): reason = 'snapshot_only'
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


def parse_forecast(data, fetched_at=None):
    now = time.time() if fetched_at is None else fetched_at
    hours = data['hourly']
    rows = []
    for index, timestamp in enumerate(hours['time']):
        if not now <= timestamp <= now+3*3600: continue
        probability = hours['precipitation_probability'][index]
        snowfall = hours['snowfall'][index]
        code = hours['weather_code'][index]
        if probability is not None and not 0 <= probability <= 100:
            raise ValueError('Invalid precipitation probability')
        snow_expected = None if code is None or snowfall is None else (code in SNOW_CODES or snowfall > 0)
        rows.append(dict(timestamp=timestamp, precipitation_probability=probability,
                         snowfall_cm=snowfall, temperature_c=hours['temperature_2m'][index],
                         snow_expected=snow_expected))
    if not rows: raise ValueError('No future forecast hours')
    return dict(status='available', fetched_at=now, hours=rows, city=CITY,
                source='Open-Meteo', source_url='https://open-meteo.com/', calibrated=False,
                note='Вероятность осадков включает дождь и снег. Вероятность снега вычисляется отдельно по членам ансамбля и требует локальной калибровки.')


def parse_ensemble(data, now=None):
    now = time.time() if now is None else now
    hours = data['hourly']
    indices = [i for i, timestamp in enumerate(hours['time']) if now <= timestamp <= now+3*3600][:3]
    if len(indices) < 3 or data.get('hourly_units', {}).get('snowfall') != 'cm':
        raise ValueError('Missing ensemble horizon or snowfall units')
    members = []
    for key, series in hours.items():
        if key != 'snowfall' and not key.startswith('snowfall_member'):
            continue
        selected = [series[i] for i in indices]
        if all(isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 for value in selected):
            members.append(selected)
    if len(members) < 10:
        raise ValueError('Insufficient complete ensemble members')
    snowy = sum(any(value >= 0.1 for value in member) for member in members)
    return dict(status='available', snow_probability=snowy/len(members), fetched_at=now,
                member_count=len(members), snowy_members=snowy, threshold_cm_per_hour=0.1,
                period_start=hours['time'][indices[0]]-3600, period_end=hours['time'][indices[-1]],
                model='ICON EPS Global', calibrated=False,
                method='fraction_of_members_snow_any_hour')


async def fetch_ensemble(client, basis):
    for attempt in range(2):
        try:
            response = await asyncio.wait_for(client.get('https://ensemble-api.open-meteo.com/v1/ensemble', params={
                'latitude': CITY['latitude'], 'longitude': CITY['longitude'],
                'hourly': 'snowfall', 'models': 'icon_global', 'forecast_days': 2,
                'timeformat': 'unixtime', 'timezone': 'GMT',
            }), 25)
            response.raise_for_status()
            return parse_ensemble(response.json(), basis)
        except asyncio.CancelledError:
            raise
        except Exception:
            if attempt == 1:
                raise
            await asyncio.sleep(1.5)


class WeatherOutlook:
    def __init__(self, path='/data/forecast.json'):
        self.path = Path(path)
        self.data = None
        self.error = None
        try: self.data = json.loads(self.path.read_text())
        except (OSError, ValueError): pass

    def observation(self):
        if self.data is None:
            return dict(status='unavailable', error=self.error, source='Open-Meteo', hours=[])
        stale = time.time()-self.data['fetched_at'] > 3600 or not self.data['hours'] or self.data['hours'][0]['timestamp'] <= time.time()
        return dict(self.data, status='stale' if stale else 'available', error=self.error)

    async def run(self, client=None):
        if client is None:
            async with httpx.AsyncClient(timeout=15, limits=httpx.Limits(max_connections=1)) as weather_client:
                await self.run(weather_client)
            return
        while True:
            try:
                response = await asyncio.wait_for(client.get('https://api.open-meteo.com/v1/forecast', params={
                    'latitude': CITY['latitude'], 'longitude': CITY['longitude'],
                    'hourly': 'temperature_2m,precipitation_probability,snowfall,weather_code',
                    'forecast_days': 2, 'timeformat': 'unixtime', 'timezone': 'GMT',
                }), 25)
                response.raise_for_status()
                result = parse_forecast(response.json())
                try:
                    result['ensemble'] = await fetch_ensemble(client, result['fetched_at'])
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    previous = (self.data or {}).get('ensemble', {})
                    same_horizon = previous.get('period_end') == result['hours'][-1]['timestamp'] and previous.get('period_start') == result['hours'][0]['timestamp']-3600
                    if previous.get('status') == 'available' and same_horizon:
                        result['ensemble'] = dict(previous, cached=True, refresh_error=f'{type(exc).__name__}: {exc}',
                                                  fetched_at=previous.get('fetched_at', self.data['fetched_at']))
                    else:
                        result['ensemble'] = dict(status='unavailable', error=f'{type(exc).__name__}: {exc}')
                temporary = self.path.with_suffix('.tmp')
                await asyncio.to_thread(temporary.write_text, json.dumps(result))
                await asyncio.to_thread(temporary.replace, self.path)
                self.data = result
                self.error = result.get('ensemble', {}).get('error') or result.get('ensemble', {}).get('refresh_error')
            except asyncio.CancelledError: raise
            except Exception as exc: self.error = f'{type(exc).__name__}: {exc}'
            await asyncio.sleep(min(900, 3600-time.time()%3600+1) if self.error is None else 120)
