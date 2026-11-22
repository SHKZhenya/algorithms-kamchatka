from datetime import datetime
import unittest
from unittest.mock import patch

import httpx

from storm_warnings import KAMCHATKA, SOURCE, StormWarnings, parse_bulletin


def bulletin(cell):
    return f'''<table><tr><th>Субъект РФ в ДФО</th><th>Штормовые предупреждения</th></tr>
      <tr><td>Сахалинская область</td><td>22 ноября сильное волнение.</td></tr>
      <tr><td><strong>Камчатский край</strong>&nbsp;</td><td>{cell}</td></tr>
    </table>'''


class StormWarningsTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_extracts_region_and_tracks_dates_across_midnight(self):
        def respond(request):
            self.assertEqual(str(request.url), SOURCE['source_url'])
            return httpx.Response(200, text=bulletin('''
                <p>20 ноября в г. Петропавловске-Камчатском сильный снег.</p>
                <p>В период 22–23 ноября на юге края ожидается метель.</p>
                <p>На побережье ожидается сильный ветер.</p>
            '''))

        now = datetime(2026, 11, 22, 1, tzinfo=KAMCHATKA).timestamp()
        warnings = StormWarnings()
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with patch('storm_warnings.time.time', return_value=now):
                await warnings.refresh(client)
                data = warnings.observation()
                self.assertEqual(data['status'], 'available')
                old, current, unknown = data['items']
                self.assertTrue(old['city_mentioned'])
                self.assertEqual(old['date_status'], 'past')
                self.assertFalse(current['city_mentioned'])
                self.assertEqual(current['date_status'], 'today')
                self.assertEqual(current['last_date'], '2026-11-23')
                self.assertEqual(unknown['date_status'], 'unknown')
            with patch('storm_warnings.time.time', return_value=now + 2 * 86400):
                data = warnings.observation()
                self.assertEqual(data['status'], 'stale')
                self.assertEqual(data['items'][1]['date_status'], 'past')

    async def test_invalid_update_keeps_previous_bulletin(self):
        responses = iter([bulletin('23 ноября ожидается метель.'), '<html>Сайт на обслуживании</html>'])
        warnings = StormWarnings()
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, text=next(responses)))) as client:
            await warnings.refresh(client)
            with self.assertRaises(ValueError):
                await warnings.refresh(client)
        self.assertEqual(warnings.observation()['items'][0]['text'], '23 ноября ожидается метель.')

    async def test_empty_row_is_valid_and_new_year_date_is_resolved(self):
        self.assertEqual(parse_bulletin(bulletin('&nbsp;—')), [])
        warnings = StormWarnings()
        now = datetime(2026, 12, 31, 2, tzinfo=KAMCHATKA).timestamp()
        warnings.bulletin = now, ['31 декабря – 1 января на юге края сильный ветер.']
        with patch('storm_warnings.time.time', return_value=now):
            self.assertEqual(warnings.observation()['items'][0]['last_date'], '2027-01-01')

    def test_undated_list_item_uses_the_bulletin_heading_date(self):
        warnings = StormWarnings()
        now = datetime(2026, 11, 22, 2, tzinfo=KAMCHATKA).timestamp()
        warnings.bulletin = now, parse_bulletin(bulletin('''
            <p>20 ноября на юге края ожидаются опасные явления:</p>
            <p>- ночью 21 ноября на севере ожидается метель;</p>
            <p>- утром в г. Петропавловске-Камчатском ожидается сильный снег.</p>
            <p>Дополнительно ожидается сильный ветер.</p>
        '''))
        with patch('storm_warnings.time.time', return_value=now):
            heading, dated, city, separate = warnings.observation()['items']
        self.assertEqual(heading['last_date'], '2026-11-20')
        self.assertEqual(dated['last_date'], '2026-11-21')
        self.assertTrue(city['city_mentioned'])
        self.assertEqual(city['date_status'], 'past')
        self.assertEqual(city['last_date'], '2026-11-20')
        self.assertEqual(separate['date_status'], 'unknown')
