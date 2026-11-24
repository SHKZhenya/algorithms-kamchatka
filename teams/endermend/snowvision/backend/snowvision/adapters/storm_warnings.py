"""Fetch the Kamchatka entry of Roshydromet's Far East emergency bulletin."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import re
import time

import httpx


SOURCE = {
    'source': 'Росгидромет · бюллетень по Дальнему Востоку',
    'source_url': 'https://dfo.meteorf.gov.ru/ekstrennaya-informatsiya/',
}
REGION = 'Камчатский край'
REFRESH_SECONDS = 600
CONNECTION_RETRY_SECONDS = 60
MAX_BODY_BYTES = 2 * 1024 * 1024
KAMCHATKA = timezone(timedelta(hours=12))
MONTHS = 'января февраля марта апреля мая июня июля августа сентября октября ноября декабря'.split()
DATE = re.compile(
    rf'\b(?:(\d{{1,2}})\s*(?:[-–—]|и\b|по\b)\s*)?(\d{{1,2}})\s+({"|".join(MONTHS)})\b'
    r'(?:\s+(\d{4})\b)?', re.I,
)
CITY = re.compile(
    r'\bпетропавловск(?:а|у|е|ом)?[\s‑–-]+камчатск\w*\b'
    r'|\bпетропавловск(?:ий|ого|ому|ом)\s+городск', re.I,
)
EMPTY = {'', '-', '—', '–', 'нет', 'предупреждений нет', 'не объявлены', 'не ожидаются'}
LIST_ITEM = re.compile(r'^(?:[-–—−]\s|•)')


class BulletinTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = []
        elif tag in ('br', 'p', 'div', 'li') and self.cell is not None:
            self.cell.append('\n')

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(re.sub(r'\s+', ' ', data))

    def handle_endtag(self, tag):
        if tag in ('p', 'div', 'li') and self.cell is not None:
            self.cell.append('\n')
        elif tag in ('td', 'th') and self.cell is not None:
            self.row.append([' '.join(part.split()) for part in ''.join(self.cell).splitlines() if part.strip()])
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None
            self.cell = None


def parse_bulletin(html):
    parser = BulletinTable()
    parser.feed(html)
    parser.close()
    if not any('штормовые предупреждения' in ' '.join(cell).casefold()
               for row in parser.rows for cell in row):
        raise ValueError('Storm warning table not found')
    for row in parser.rows:
        if len(row) >= 2 and ' '.join(row[0]).strip().casefold() == REGION.casefold():
            return list(dict.fromkeys(text for text in row[1] if text.casefold().rstrip('.') not in EMPTY))
    raise ValueError('Kamchatka warning cell not found')


def warning_dates(text, reference):
    dates = []
    for first_day, day, month, year in DATE.findall(text):
        years = (int(year),) if year else range(reference.year - 1, reference.year + 2)
        month_number = MONTHS.index(month.lower()) + 1
        for value in filter(None, (first_day, day)):
            candidates = []
            for candidate_year in years:
                try:
                    candidates.append(date(candidate_year, month_number, int(value)))
                except ValueError:
                    continue
            if candidates:
                dates.append(min(candidates, key=lambda value: abs(value - reference)))
    return dates


class StormWarnings:
    def __init__(self):
        self.bulletin = None
        self.error = None

    def observation(self):
        result = dict(SOURCE, region=REGION, error=self.error, items=[])
        if self.bulletin is None:
            return dict(result, status='unavailable')
        fetched_at, messages = self.bulletin
        now = time.time()
        today = datetime.fromtimestamp(now, KAMCHATKA).date()
        reference = datetime.fromtimestamp(fetched_at, KAMCHATKA).date()
        list_dates = []
        for text in messages:
            dates = warning_dates(text, reference)
            if LIST_ITEM.match(text):
                dates = dates or list_dates
            else:
                list_dates = dates if text.endswith(':') else []
            last_date = max(dates, default=None)
            if last_date is None:
                date_status = 'unknown'
            elif last_date < today:
                date_status = 'past'
            elif min(dates) > today:
                date_status = 'upcoming'
            else:
                date_status = 'today'
            result['items'].append(dict(
                text=text, city_mentioned=bool(CITY.search(text)),
                date_status=date_status, last_date=last_date.isoformat() if last_date else None,
            ))
        stale = self.error is not None or now - fetched_at > 2 * REFRESH_SECONDS
        return dict(result, status='stale' if stale else 'available', fetched_at=fetched_at)

    async def refresh(self, client):
        body = bytearray()
        async with client.stream('GET', SOURCE['source_url']) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    raise ValueError('Bulletin response too large')
            html = body.decode(response.encoding or 'utf-8')
        self.bulletin = time.time(), parse_bulletin(html)
        self.error = None

    async def run(self):
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            while True:
                interval = REFRESH_SECONDS
                try:
                    await asyncio.wait_for(self.refresh(client), timeout=15)
                except httpx.HTTPStatusError as exc:
                    self.error = f'http_{exc.response.status_code}'
                except (httpx.RequestError, TimeoutError):
                    self.error = 'connection_failed'
                    interval = CONNECTION_RETRY_SECONDS
                except (ValueError, LookupError):
                    self.error = 'invalid_response'
                await asyncio.sleep(interval)
