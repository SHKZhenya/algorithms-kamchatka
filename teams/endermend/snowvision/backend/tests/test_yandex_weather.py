import unittest
from unittest.mock import patch

import httpx

from snowvision.adapters.yandex_weather import MAX_CACHE_AGE, MAX_OBSERVATION_AGE, YandexWeather


class YandexWeatherTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_authentication_mapping_and_cache_age(self):
        weather = YandexWeather('test-key')

        def receive(request):
            self.assertEqual(request.url.host, 'api.weather.yandex.ru')
            self.assertEqual(request.headers['X-Yandex-Weather-Key'], 'test-key')
            self.assertNotIn('test-key', str(request.url))
            self.assertEqual(request.url.params['hours'], 'true')
            self.assertEqual(request.url.params['limit'], '2')
            return httpx.Response(200, json={
                'fact': {'obs_time': 100000, 'prec_type': 2, 'temp': -1},
                'forecasts': [
                    {'hours': [{'hour_ts': 100000, 'prec_type': 1},
                               {'hour_ts': 100800, 'temp': -2, 'prec_type': 3, 'prec_mm': 1.2}]},
                    {'hours': [{'hour_ts': 108000, 'temp': -1, 'prec_type': 0},
                               {'hour_ts': 104400, 'prec_type': 2, 'prec_prob': 80, 'fresh_snow_mm': 3},
                               {'hour_ts': 111600, 'prec_type': 4}]},
                ],
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
            with patch('snowvision.adapters.yandex_weather.time.time', return_value=100040):
                await weather.refresh(client)
                self.assertEqual(weather.observation()['precipitation'], 'sleet')
                forecast = weather.forecast()
                self.assertEqual(forecast['status'], 'available')
                self.assertEqual(forecast['source'], 'Яндекс Погода')
                snow, sleet, dry = forecast['hours']
                self.assertEqual((snow['timestamp'], sleet['timestamp'], dry['timestamp']), (100800, 104400, 108000))
                self.assertEqual(snow['precipitation_mm'], 1.2)
                self.assertIsNone(snow['snowfall_cm'])
                self.assertIsNone(snow['precipitation_probability'])
                self.assertEqual(sleet['snowfall_cm'], 0.3)
                self.assertEqual(sleet['precipitation_probability'], 80)
                self.assertTrue(sleet['snow_expected'])
            with patch('snowvision.adapters.yandex_weather.time.time', return_value=100040 + MAX_CACHE_AGE + 1):
                self.assertEqual(weather.observation()['status'], 'stale')
                self.assertNotIn('precipitation', weather.observation())
                self.assertEqual(weather.forecast()['status'], 'stale')
                self.assertEqual(len(weather.forecast()['hours']), 2)

    async def test_missing_forecast_does_not_replace_last_hours_or_hide_fresh_observation(self):
        weather = YandexWeather('test-key')
        payload = {
            'fact': {'obs_time': 100000, 'prec_type': 3},
            'forecasts': [{'hours': [{'hour_ts': 103600, 'temp': -2}]}],
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=payload))) as client:
            with patch('snowvision.adapters.yandex_weather.time.time', return_value=100040):
                await weather.refresh(client)
                self.assertEqual(weather.forecast()['status'], 'available')
            payload.update(fact={'obs_time': 100050, 'prec_type': 1}, forecasts=[{'hours': ''}])
            with patch('snowvision.adapters.yandex_weather.time.time', return_value=100060):
                await weather.refresh(client)
                self.assertEqual(weather.forecast()['fetched_at'], 100040)
                self.assertEqual(weather.forecast()['error'], 'invalid_response')
                self.assertEqual(weather.observation()['precipitation'], 'rain')

    async def test_observation_age_is_independent_of_download_time(self):
        now = 100000
        weather = YandexWeather('test-key')
        response = httpx.Response(200, json={'fact': {
            'obs_time': now - MAX_OBSERVATION_AGE - 1, 'prec_type': 3,
        }})
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
            with patch('snowvision.adapters.yandex_weather.time.time', return_value=now):
                await weather.refresh(client)
                self.assertEqual(weather.observation()['status'], 'stale')
                self.assertNotIn('precipitation', weather.observation())

    async def test_invalid_response_does_not_replace_last_observation(self):
        weather = YandexWeather('test-key')
        fact = {'obs_time': 100000, 'prec_type': 3}
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={'fact': fact}))) as client:
            with patch('snowvision.adapters.yandex_weather.time.time', return_value=100040):
                await weather.refresh(client)
                for invalid in ({'obs_time': 99999, 'prec_type': 1}, {'obs_time': 100001, 'prec_type': True}):
                    fact.update(invalid)
                    with self.assertRaises(ValueError):
                        await weather.refresh(client)
                    self.assertEqual(weather.observation()['precipitation'], 'snow')
