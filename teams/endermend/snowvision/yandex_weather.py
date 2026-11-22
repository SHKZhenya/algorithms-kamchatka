"""Yandex observations and hourly forecast for the city's reference point."""
import asyncio
import math
import time

import httpx

from city import CITY

URL = 'https://api.weather.yandex.ru/v2/forecast'
PRECIPITATION = {0: 'none', 1: 'rain', 2: 'sleet', 3: 'snow', 4: 'hail'}
REFRESH_SECONDS = 600
CONNECTION_RETRY_SECONDS = 60
MAX_CACHE_AGE = 2 * REFRESH_SECONDS
MAX_OBSERVATION_AGE = 3600
SOURCE = {'source': 'Яндекс Погода', 'source_url': 'https://yandex.ru/pogoda/'}


def numeric(value):
    return value if type(value) is int or (type(value) is float and math.isfinite(value)) else None


def parse_hours(days, now):
    if not isinstance(days, list):
        raise ValueError('Missing Yandex forecast days')
    rows = {}
    for day in days:
        hours = day.get('hours') if isinstance(day, dict) else None
        if not isinstance(hours, list):
            continue
        for hour in hours:
            if not isinstance(hour, dict):
                continue
            timestamp = hour.get('hour_ts')
            if type(timestamp) is not int or not now <= timestamp <= now + 3 * 3600:
                continue
            code = hour.get('prec_type')
            precipitation = PRECIPITATION.get(code) if type(code) is int else None
            probability = numeric(hour.get('prec_prob'))
            amount_mm = numeric(hour.get('prec_mm'))
            snow_mm = numeric(hour.get('fresh_snow_mm'))
            temperature = numeric(hour.get('temp'))
            row = dict(
                timestamp=timestamp, temperature_c=temperature, precipitation=precipitation,
                precipitation_probability=probability if probability is not None and 0 <= probability <= 100 else None,
                precipitation_mm=amount_mm if amount_mm is not None and amount_mm >= 0 else None,
                snowfall_cm=snow_mm / 10 if snow_mm is not None and snow_mm >= 0 else None,
                snow_expected=precipitation in ('snow', 'sleet') if precipitation is not None else None,
            )
            if any(value is not None for key, value in row.items() if key != 'timestamp'):
                rows[timestamp] = row
    if not rows:
        raise ValueError('No future Yandex forecast hours')
    return [rows[timestamp] for timestamp in sorted(rows)[:3]]


class YandexWeather:
    def __init__(self, key):
        self.key = key
        self.data = None
        self.forecast_data = None
        self.error = None
        self.forecast_error = None

    def observation(self):
        if self.data is None:
            return {'status': 'unavailable' if self.key else 'disabled', 'error': self.error}
        now = time.time()
        fresh = now - self.data['fetched_at'] <= MAX_CACHE_AGE and (
            -60 <= now - self.data['observed_at'] <= MAX_OBSERVATION_AGE
        )
        if not fresh:
            return {'status': 'stale', 'error': self.error, 'observed_at': self.data['observed_at']}
        return dict(self.data, status='available', error=self.error)

    def forecast(self):
        result = dict(SOURCE, city=CITY, calibrated=False, hours=[], error=self.forecast_error)
        if self.forecast_data is None:
            return dict(result, status='unavailable' if self.key else 'disabled')
        now = time.time()
        fetched_at = self.forecast_data['fetched_at']
        hours = [hour for hour in self.forecast_data['hours'] if hour['timestamp'] >= now]
        stale = now - fetched_at > MAX_CACHE_AGE or not hours
        return dict(result, fetched_at=fetched_at, hours=hours, status='stale' if stale else 'available')

    async def refresh(self, client):
        response = await client.get(
            URL,
            params={'lat': CITY['latitude'], 'lon': CITY['longitude'], 'limit': 2, 'hours': 'true'},
            headers={'X-Yandex-Weather-Key': self.key},
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError:
            self.forecast_error = 'invalid_response'
            raise
        now = time.time()
        if not isinstance(payload, dict):
            self.forecast_error = 'invalid_response'
            raise ValueError('Invalid Yandex weather response')
        try:
            hours = parse_hours(payload.get('forecasts'), now)
        except ValueError:
            self.forecast_error = 'invalid_response'
        else:
            self.forecast_data = dict(fetched_at=now, hours=hours)
            self.forecast_error = None

        fact = payload.get('fact')
        if not isinstance(fact, dict):
            raise ValueError('Missing Yandex weather observation')
        code = fact.get('prec_type')
        observed_at = numeric(fact.get('obs_time'))
        if (
            type(code) is not int or code not in PRECIPITATION
            or observed_at is None or not 0 < observed_at <= now + 60
            or (self.data and observed_at < self.data['observed_at'])
        ):
            raise ValueError('Invalid or out-of-order Yandex weather observation')
        self.data = dict(
            precipitation=PRECIPITATION[code], temperature_c=numeric(fact.get('temp')),
            observed_at=observed_at, fetched_at=now,
        )

    async def run(self):
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            while True:
                interval = REFRESH_SECONDS
                try:
                    await asyncio.wait_for(self.refresh(client), timeout=12)
                    self.error = None
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    self.error = self.forecast_error = f'http_{status}'
                    if status in (401, 403, 429):
                        interval = 3600
                except (httpx.RequestError, TimeoutError):
                    self.error = self.forecast_error = 'connection_failed'
                    interval = CONNECTION_RETRY_SECONDS
                except ValueError:
                    self.error = 'invalid_response'
                await asyncio.sleep(interval)
