import unittest
from city import aggregate, parse_forecast, parse_ensemble, WeatherOutlook

class CityTests(unittest.TestCase):
    def camera(self,id,point,score=.8):
        return dict(id=id,type='jpeg',city_observation=True,coordinates_lat_lon=point,
                    observation=dict(source_status='online',snow_probability=score,confidence=.8,last_analysis_at=1000))
    def test_same_location_is_not_independent(self):
        rows=[self.camera('a',[53.05,158.64]),self.camera('b',[53.05,158.64])]
        result=aggregate(rows,1000)
        self.assertEqual(result['independent_zones'],1)
        self.assertIsNone(result['snow_score'])
    def test_spatial_groups_and_exclusions(self):
        rows=[self.camera('a',[53.05,158.64]),self.camera('b',[53.09,158.67])]
        rows.append(dict(self.camera('demo',[53.12,158.64]),type='demo'))
        result=aggregate(rows,1000)
        self.assertAlmostEqual(result['snow_score'],.8)
        self.assertEqual(result['eligible_cameras'],2)
        self.assertIsNone(aggregate(rows,2000)['snow_score'])
    def test_forecast_preserves_precipitation_probability(self):
        data={'hourly':{'time':[1100,4700],'precipitation_probability':[80,90],'snowfall':[.2,0],
                        'weather_code':[73,61],'temperature_2m':[-1,3]}}
        result=parse_forecast(data,1000)
        self.assertEqual(result['hours'][0]['precipitation_probability'],80)
        self.assertTrue(result['hours'][0]['snow_expected'])
        self.assertFalse(result['hours'][1]['snow_expected'])
        self.assertFalse(result['calibrated'])

    def test_missing_weather_probability_stays_unknown(self):
        data={'hourly':{'time':[1100],'precipitation_probability':[None],'snowfall':[0],
                        'weather_code':[0],'temperature_2m':[-1]}}
        self.assertIsNone(parse_forecast(data,1000)['hours'][0]['precipitation_probability'])

    def test_connected_nearby_cameras_form_one_zone(self):
        rows=[self.camera(str(i),[53.05+i*.006,158.64]) for i in range(3)]
        self.assertEqual(aggregate(rows,1000)['independent_zones'],1)
        self.assertEqual(aggregate(list(reversed(rows)),1000)['independent_zones'],1)

    def test_ensemble_keeps_time_correlation(self):
        hours={'time':[1100,4700,8300], 'snowfall':[0,0,0]}
        for member in range(1,11):
            hours[f'snowfall_member{member:02}']=[.2,.2,.2] if member<=5 else [0,0,0]
        result=parse_ensemble({'hourly':hours,'hourly_units':{'snowfall':'cm'}},1000)
        self.assertEqual(result['member_count'],11)
        self.assertEqual(result['snow_probability'],5/11)
        hours['snowfall_member01']=[None,.2,.2]
        self.assertEqual(parse_ensemble({'hourly':hours,'hourly_units':{'snowfall':'cm'}},1000)['member_count'],10)

    def test_cached_horizon_is_stale_after_first_hour_ends(self):
        from unittest.mock import patch
        weather=WeatherOutlook('/tmp/nonexistent-snowvision-weather-unit.json')
        weather.data={'fetched_at':1000,'hours':[{'timestamp':1100},{'timestamp':4700}]}
        with patch('city.time.time',return_value=1200):
            self.assertEqual(weather.observation()['status'],'stale')
