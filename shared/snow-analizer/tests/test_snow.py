import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import numpy as np
from pydantic import ValidationError
from shapely.geometry import Polygon, box

from snowmap.api import ManualWeather, SnowRequest, Tile, calculate, index
from snowmap.model import estimate, polygon_effects, polygon_faces
from snowmap.sources import OVERPASS_URLS, Building, OverpassError, fetch_overpass, demo_tile, parse_buildings, projection, weather, weather_cache


class SnowTest(unittest.TestCase):
    def test_demo_and_calm_wind(self):
        response = calculate(SnowRequest(manual_weather=ManualWeather(wind_speed_ms=0)))
        raster = np.array(response["snow_depth_cm"], dtype=float)
        self.assertEqual(response["building_count"], 4)
        self.assertTrue(np.isnan(raster).any())
        self.assertTrue(np.all(raster[np.isfinite(raster)] == 6))
        self.assertTrue(index().path.is_file())

    def test_opposite_wind_moves_leeward_drift(self):
        _, bounds, _ = demo_tile()
        _, _, width, height = projection(bounds)
        x, y = width/2, height/2
        building = Building(box(x-12, y-6, x+12, y+6), 15, "fixture")
        for direction, side in ((270, 1), (90, -1)):
            hourly = [{"wind_speed_ms": 8, "wind_from_deg": direction, "snowfall_cm": 2}]
            result = estimate(bounds, (building,), hourly, 120, False)
            raster = np.array(result["snow_depth_cm"], dtype=float)
            _, column = np.unravel_index(np.nanargmax(raster), raster.shape)
            self.assertGreater(side*(column+0.5-raster.shape[1]/2), 0)

    def test_overlapping_flanks_reduce_accumulation_in_passage(self):
        _, bounds, _ = demo_tile()
        _, _, width, height = projection(bounds)
        x, y = width/2, height/2
        north = Building(box(x-12, y+5, x+12, y+17), 15, "fixture")
        south = Building(box(x-12, y-17, x+12, y-5), 15, "fixture")
        hourly = [{"wind_speed_ms": 8, "wind_from_deg": 270, "snowfall_cm": 2}]
        single = estimate(bounds, (north,), hourly, 120, False)
        pair = estimate(bounds, (north, south), hourly, 120, False)
        row, col = single["rows"]//2, single["cols"]//2
        self.assertLess(pair["snow_depth_cm"][row][col], single["snow_depth_cm"][row][col])

    def test_no_new_snow(self):
        response = calculate(SnowRequest(manual_weather=ManualWeather(snowfall_cm_h=0)))
        self.assertEqual(response["max_snow_depth_cm"], 0)

    def test_live_foam_uses_tile_field_without_templates(self):
        def field(buildings, width, height, rows, cols, direction, speed):
            self.assertEqual(len(buildings), 4)
            self.assertEqual((direction, speed), (270, 8))
            return np.full((rows, cols), 0.4), {"iterations": 120}

        request = SnowRequest(run_foam=True, hours=1, resolution=24)
        with (
            patch("snowmap.model.solve_tile", side_effect=field) as solve,
            patch("snowmap.model.kernels", side_effect=AssertionError("Live run must bypass templates")),
            patch("snowmap.model.rectangle", side_effect=AssertionError("Live run must use polygon faces")),
        ):
            result = calculate(request)
        solve.assert_called_once()
        self.assertEqual(result["model"], "openfoam_tile")
        self.assertEqual(result["foam_run"], {"iterations": 120})
        baseline = calculate(request.model_copy(update={"run_foam": False, "use_foam": False}))
        self.assertNotEqual(result["snow_depth_cm"], baseline["snow_depth_cm"])

    def test_polygon_wake_starts_at_recessed_leeward_facade(self):
        building = Polygon([(0, 0), (30, 0), (30, 5), (5, 5), (5, 30), (0, 30)])
        x, y = np.array([21.0]), np.array([24.0])
        east, north = np.array([1, 0]), np.array([0, 1])
        wake, _ = polygon_effects(polygon_faces(building), 10, x, y, east, north)
        rectangle_wake, _ = polygon_effects(polygon_faces(building.envelope), 10, x, y, east, north)
        self.assertAlmostEqual(wake[0], 0.95)
        self.assertEqual(rectangle_wake[0], 0.0)

    def test_overpass_switches_provider_after_rate_limit(self):
        responses = [
            httpx.Response(429, request=httpx.Request("POST", OVERPASS_URLS[0])),
            httpx.Response(200, json={"elements": []}, request=httpx.Request("POST", OVERPASS_URLS[1])),
        ]
        with patch.dict(os.environ, {"OVERPASS_URL": ""}), patch(
            "snowmap.sources.httpx.post", side_effect=responses
        ) as post:
            self.assertEqual(fetch_overpass("[out:json];out;"), [])
        self.assertEqual([call.args[0] for call in post.call_args_list], list(OVERPASS_URLS))
        self.assertEqual(post.call_args.kwargs["headers"]["User-Agent"], "KamchaSnowMap/0.1")

    def test_overpass_distinguishes_connection_and_read_timeouts(self):
        with patch.dict(os.environ, {"OVERPASS_URL": ""}), patch(
            "snowmap.sources.httpx.post",
            side_effect=[httpx.ConnectTimeout("connect"), httpx.ReadTimeout("read")],
        ):
            with self.assertRaisesRegex(OverpassError, "подключение за 5 с.*ответ за 25 с"):
                fetch_overpass("[out:json];out;")

    def test_overpass_reports_connection_failure_for_explicit_url(self):
        override = "https://example.com/api/interpreter"
        with patch.dict(os.environ, {"OVERPASS_URL": override}), patch(
            "snowmap.sources.httpx.post", side_effect=httpx.ConnectError("DNS unavailable")
        ) as post:
            with self.assertRaisesRegex(OverpassError, "DNS"):
                fetch_overpass("[out:json];out;")
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], override)

    def test_osm_tile_uses_weather_provider(self):
        (z, x, y), bounds, buildings = demo_tile()
        hourly = [{"wind_speed_ms": 0, "wind_from_deg": 0,
                   "snowfall_cm": 3, "precipitation_mm": 1}]
        with (
            patch("snowmap.api.osm_buildings", return_value=buildings) as osm,
            patch("snowmap.api.weather", return_value=hourly) as forecast,
        ):
            response = calculate(SnowRequest(tile=Tile(z=z, x=x, y=y), hours=1))
        osm.assert_called_once_with(bounds)
        forecast.assert_called_once_with(bounds, 1)
        self.assertEqual(response["weather_source"], "Open-Meteo")
        self.assertEqual(response["max_snow_depth_cm"], 3)

    def test_out_of_range_tile(self):
        with self.assertRaises(ValidationError):
            Tile(z=17, x=2**17, y=0)

    def test_osm_multipolygon_courtyard(self):
        _, bounds, _ = demo_tile()
        lon, lat, *_ = bounds

        def ring(delta, length):
            return [{"lon": lon+delta+x*length, "lat": lat+delta+y*length}
                    for x, y in ((0, 0), (1, 0), (1, 1), (0, 1), (0, 0))]

        outer, inner = ring(0.0001, 0.0004), ring(0.0002, 0.0001)
        elements = [
            {"type": "way", "id": 1, "geometry": outer, "tags": {"building": "yes"}},
            {"type": "relation", "id": 2, "tags": {"building": "yes", "building:levels": "5"},
             "members": [
                 {"type": "way", "ref": 1, "role": "outer", "geometry": outer},
                 {"type": "way", "ref": 3, "role": "inner", "geometry": inner},
             ]},
        ]
        building, = parse_buildings(elements, bounds)
        self.assertEqual(len(building.footprint.interiors), 1)
        self.assertEqual(building.height, 15)

    def test_forecast_uses_future_hour_ending_times(self):
        now = datetime(2026, 1, 1, 12, 25, tzinfo=timezone.utc)
        start = int(now.timestamp()) // 3600 * 3600
        fixture = {"hourly": {"time": [start, start+3600, start+7200],
                              "wind_speed_10m": [1, 8, 9], "wind_direction_10m": [0, 90, 180],
                              "snowfall": [10, 2, 4], "precipitation": [6, 2, 3]}}
        _, bounds, _ = demo_tile()
        weather_cache.entries.clear()
        with patch("snowmap.sources.datetime") as clock, patch("snowmap.sources.httpx.get") as get:
            clock.now.return_value = now
            clock.fromtimestamp.side_effect = datetime.fromtimestamp
            get.return_value = httpx.Response(200, json=fixture, request=httpx.Request("GET", "https://example.com"))
            rows = weather(bounds, 2)
        self.assertEqual([row["snowfall_cm"] for row in rows], [2, 4])
        self.assertEqual(rows[0]["wind_from_deg"], 90)
        self.assertEqual(rows[1]["precipitation_mm"], 3)


if __name__ == "__main__":
    unittest.main()
