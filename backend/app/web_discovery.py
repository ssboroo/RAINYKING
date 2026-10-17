"""Bounded public website discovery without an upstream API."""
import asyncio
import hashlib
import json
from collections import deque
from urllib.parse import urljoin, urlsplit, urlunsplit, urlencode
from urllib.robotparser import RobotFileParser
import httpx
from bs4 import BeautifulSoup
from .connectors import validate_public_url, ConnectorError, parse_public_payload

AGENT = 'RAINYTransparencyBot'
MAX_BYTES = 5_000_000


def in_scope(url, host):
    try:
        candidate = validate_public_url(url)
        parsed = urlsplit(url)
        if parsed.port not in (None, 443): return False
        return candidate.removeprefix('www.') == host.removeprefix('www.')
    except (ConnectorError, ValueError):
        return False


def canonical_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path or '/', parts.query, ''))


async def download(client, url, host, allowed=None):
    for _ in range(4):
        if not in_scope(url, host): raise ConnectorError('Redirect/domain нь зөвшөөрсөн эх сурвалжаас гадуур.')
        if allowed is not None and not allowed(url): raise ConnectorError('robots.txt энэ URL-д хандахыг зөвшөөрөөгүй.')
        async with client.stream('GET', url) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers.get('location', ''))
                continue
            response.raise_for_status()
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_BYTES: raise ConnectorError('5 MB хэмжээнээс их баримт.')
            return bytes(raw), response.headers.get('content-type', ''), str(response.url)
    raise ConnectorError('Redirect хязгаараас хэтэрсэн.')


def discover_links(raw, url, host, query):
    soup = BeautifulSoup(raw, 'html.parser')
    links = []
    for a in soup.select('a[href]'):
        target = canonical_url(urljoin(url, a['href']))
        if in_scope(target, host):
            label = a.get_text(' ', strip=True).casefold()
            score = sum(token in label for token in query.casefold().split())
            if urlsplit(target).path.lower().endswith(('.pdf', '.xlsx', '.csv', '.json')): score += 1
            links.append((score, target))
    # Only use an observed GET search form and its observed query field; never guess endpoint names.
    for form in soup.find_all('form'):
        if str(form.get('method', 'get')).lower() != 'get': continue
        search = next((tag for tag in form.select('input[name]') if tag.get('type') == 'search' or tag.get('name') in ('q', 'search', 'keyword')), None)
        if search is None or not query: continue
        target = urljoin(url, form.get('action') or url)
        if not in_scope(target, host): continue
        params = {tag['name']: tag.get('value', '') for tag in form.select('input[type=hidden][name]')}
        params[search['name']] = query
        parts = urlsplit(target)
        links.append((100, urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ''))))
    return [url for _, url in sorted(links, key=lambda item: -item[0])]


async def crawl_public_site(start_url, query='', max_pages=5, max_depth=2, client=None, delay=.5):
    host = validate_public_url(start_url)
    if not in_scope(start_url, host): raise ConnectorError('URL/порт зөвшөөрөгдөөгүй.')
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), follow_redirects=False,
                                   headers={'User-Agent': AGENT+'/1.0', 'Accept': 'text/html,application/pdf,text/csv,application/json,*/*'})
    try:
        parts = urlsplit(start_url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, '/robots.txt', '', ''))
        parser = RobotFileParser()
        try:
            robots, _, _ = await download(client, robots_url, host)
            parser.parse(robots.decode('utf-8', errors='replace').splitlines())
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                return {'status': 'robots_unavailable', 'pages': [], 'records': [], 'note': 'robots.txt хандалтыг баталгаажуулж чадсангүй.'}
            parser.parse([])
        except (httpx.HTTPError, ConnectorError):
            return {'status': 'robots_unavailable', 'pages': [], 'records': [], 'note': 'robots.txt хандалтыг баталгаажуулж чадсангүй.'}
        allowed = lambda url: parser.can_fetch(AGENT, url)
        queue = deque([(canonical_url(start_url), 0)]); queued = {canonical_url(start_url)}
        pages = []; records = []; tokens = query.casefold().split()
        crawl_delay = max(delay, min(parser.crawl_delay(AGENT) or 0, 30))
        if crawl_delay > 5:
            return {'status': 'deferred_crawl_delay', 'pages': [], 'records': [], 'note': 'Эх сурвалжийн crawl-delay урт; background worker тохируулах шаардлагатай.'}
        while queue and len(pages) < max_pages:
            url, depth = queue.popleft()
            if not allowed(url):
                pages.append({'url': url, 'status': 'robots_denied'}); continue
            try:
                if pages: await asyncio.sleep(crawl_delay)
                raw, mime, final_url = await download(client, url, host, allowed)
                fmt, rows = parse_public_payload(raw, mime, final_url, max_rows=500)
                limited_html = fmt == 'html_text' and bool(rows) and len(str(rows[0].get('text', ''))) < 150
                matched = []
                for index, row in enumerate(rows):
                    text = json.dumps(row, ensure_ascii=False, default=str).casefold()
                    if not limited_html and all(token in text for token in tokens):
                        matched.append((index, row))
                for index, row in matched:
                    records.append({'url': final_url, 'format': fmt, 'row_index': index,
                                    'row': row, 'content_sha256': hashlib.sha256(raw).hexdigest()})
                status = 'read' if rows else ('needs_ocr' if fmt == 'pdf' else 'empty')
                if limited_html:
                    status = 'limited_html_possible_javascript'
                pages.append({'url': final_url, 'status': status, 'format': fmt,
                              'rows_read': len(rows), 'matched_rows': len(matched), 'bytes': len(raw)})
                if fmt.startswith('html') and depth < max_depth:
                    for link in discover_links(raw, final_url, host, query):
                        if link not in queued and len(queued) < 200:
                            queued.add(link); queue.append((link, depth+1))
            except Exception as exc:
                pages.append({'url': url, 'status': 'failed', 'error': type(exc).__name__})
        return {'status': 'partial' if queue or any(p['status'] != 'read' for p in pages) else 'bounded_complete',
                'pages': pages, 'records': records, 'queued_remaining': len(queue),
                'note': 'Хязгаартай нийтийн crawl; бүх сайтыг бүрэн шалгасан гэсэн үг биш. PDF зураг/OCR болон JavaScript дүрслэл тусдаа adapter шаарддаг.'}
    finally:
        if own_client: await client.aclose()
