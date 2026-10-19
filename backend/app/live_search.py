"""Bounded, cached public reads; no admin credentials or database writes."""
import asyncio
from collections import OrderedDict
from copy import deepcopy
import time
from .connectors import connector_catalog
from .web_discovery import crawl_public_site

_CACHE = OrderedDict()
_INFLIGHT = {}
_LIMIT = asyncio.Semaphore(2)
TTL = 600


async def search_source(code, query):
    source = next((s for s in connector_catalog() if s['code'] == code), None)
    if source is None:
        raise ValueError('Unknown source')
    query = ' '.join(query.split())
    if len(query) < 2:
        raise ValueError('Search requires at least two characters')
    key = (code, query.casefold())
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < (TTL if cached[1].get('records') else 30):
        return {**deepcopy(cached[1]), 'cached': True}
    if key in _INFLIGHT:
        return deepcopy(await asyncio.shield(_INFLIGHT[key]))
    # Reject excess work rather than building an unbounded queue on public requests.
    if len(_INFLIGHT) >= 4:
        return {'source_code': code, 'source_name': source['name'], 'status': 'busy', 'records': [], 'pages': [], 'note': 'Таталтын ачаалалтай. Түр хүлээгээд дахин хайна уу.'}

    async def run():
        async with _LIMIT:
            try:
                async with asyncio.timeout(140 if code == 'shilendans' else 70):
                    if code == 'shilendans':
                        from .shilendans_browser import search_organizations
                        result = await search_organizations(query)
                    else:
                        result = await crawl_public_site(source['public_web_url'], query, max_pages=3, max_depth=2)
                records = []
                for row in result.pop('records')[:30]:
                    from .record_search import record_title
                    text = ' · '.join(str(v) for v in row['row'].values() if v is not None)
                    # Show the passage around the query, not just the beginning of a long page.
                    at = text.casefold().find(query.casefold().split()[0])
                    start = max(0, at - 120)
                    records.append({**row, 'title': record_title({'raw': row['row']}, source['name'])[:220],
                                    'snippet': text[start:start+800]})
                output = {**result, 'source_code': code, 'source_name': source['name'],
                          'records': records, 'cached': False, 'query': query,
                          'scope': 'Нийтийн вебийн 3 хүртэл хуудас; бүх санг бүрэн шалгаагүй. Вебийн тааралтыг албан ID холбоос гэж үзэхгүй.'}
            except TimeoutError:
                output = {'source_code': code, 'source_name': source['name'], 'status': 'timeout', 'records': [], 'pages': [], 'note': 'Эх сурвалжийн хариу удаан байна. Дахин оролдоно уу.'}
            except Exception as exc:
                output = {'source_code': code, 'source_name': source['name'], 'status': 'failed', 'records': [], 'pages': [], 'note': 'Нийтийн хуудсыг уншиж чадсангүй.', 'error': type(exc).__name__}
            _CACHE[key] = (time.monotonic(), deepcopy(output))
            _CACHE.move_to_end(key)
            while len(_CACHE) > 128:
                _CACHE.popitem(last=False)
            return output
    task = asyncio.create_task(run())
    _INFLIGHT[key] = task
    def finished(_):
        _INFLIGHT.pop(key, None)
    task.add_done_callback(finished)
    return deepcopy(await asyncio.shield(task))
