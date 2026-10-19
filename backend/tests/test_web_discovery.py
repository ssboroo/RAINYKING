import asyncio
import httpx
from app.web_discovery import crawl_public_site, discover_links

HOST = 'https://opendata.gov.mn'


def test_html_discovery_follows_observed_get_search_and_imports_matching_rows():
    requested = []
    def handler(request):
        requested.append(str(request.url))
        if request.url.path == '/robots.txt': return httpx.Response(200, text='User-agent: *\nDisallow: /private')
        if request.url.path == '/': return httpx.Response(200, headers={'content-type':'text/html'}, text='<html><form action="/find" method="get"><input type="search" name="keyword"></form><a href="/private">hidden</a><a href="https://evil.example/file">outside</a></html>')
        if request.url.path == '/find': return httpx.Response(200, headers={'content-type':'text/html'}, text='<table><tr><th>company_name</th><th>registration</th></tr><tr><td>Тест</td><td>AB123</td></tr></table>')
        return httpx.Response(404)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await crawl_public_site(HOST+'/', 'Тест', max_pages=5, client=client, delay=0)
    result = asyncio.run(run())
    assert len(result['records']) == 1
    assert result['records'][0]['row']['registration'] == 'AB123'
    assert any('/find?keyword=' in url for url in requested)
    assert not any('/private' in url or 'evil.example' in url for url in requested)
    assert any(p['status'] == 'robots_denied' for p in result['pages'])


def test_redirect_outside_registry_never_fetches_target():
    requested = []
    def handler(request):
        requested.append(str(request.url))
        if request.url.path == '/robots.txt': return httpx.Response(404)
        return httpx.Response(302, headers={'location':'https://evil.example/'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await crawl_public_site(HOST+'/', client=client, delay=0)
    result = asyncio.run(run())
    assert result['pages'][0]['status'] == 'failed'
    assert not any('evil.example' in url for url in requested)


def test_robots_failure_is_reported_as_not_searched():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(403))) as client:
            return await crawl_public_site(HOST+'/', client=client, delay=0)
    result = asyncio.run(run())
    assert result['status'] == 'robots_unavailable'
    assert result['records'] == []


def test_no_guessed_post_search_or_external_links():
    html = b'<form method="post" action="/search"><input name="q"></form><a href="javascript:alert(1)">x</a><a href="https://opendata.gov.mn:1234/">port</a>'
    assert discover_links(html, HOST+'/', 'opendata.gov.mn', 'test') == []


def test_javascript_placeholder_is_not_imported_as_evidence():
    def handler(request):
        if request.url.path == '/robots.txt': return httpx.Response(404)
        return httpx.Response(200, headers={'content-type':'text/html'}, text='<html><title>Portal</title><div id="app"></div><script src="app.js"></script></html>')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await crawl_public_site(HOST+'/', client=client, delay=0)
    result = asyncio.run(run())
    assert result['records'] == []
    assert result['pages'][0]['status'] == 'limited_html_possible_javascript'


def test_browser_scope_rejects_external_private_ports_and_credentials():
    from app.document_extraction import same_public_source
    assert same_public_source('https://api.shilendans.gov.mn/data', 'shilendans.gov.mn')
    assert not same_public_source('https://shilendans.gov.mn.evil.test/', 'shilendans.gov.mn')
    assert not same_public_source('http://shilendans.gov.mn/', 'shilendans.gov.mn')
    assert not same_public_source('https://shilendans.gov.mn:8080/', 'shilendans.gov.mn')
    assert not same_public_source('https://user:password@shilendans.gov.mn/', 'shilendans.gov.mn')


def test_dynamic_page_uses_renderer_and_keeps_extraction_provenance(monkeypatch):
    from app import document_extraction
    async def render(url, host, allowed):
        assert allowed(url)
        return '<html><table><tr><th>company_name</th></tr><tr><td>Dynamic company</td></tr></table></html>'.encode(), url
    monkeypatch.setattr(document_extraction, 'render_public_page', render)
    def handler(request):
        if request.url.path == '/robots.txt': return httpx.Response(404)
        return httpx.Response(200, headers={'content-type':'text/html'}, text='<html><div id="app"></div></html>')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await crawl_public_site(HOST+'/', 'Dynamic', client=client, delay=0)
    result = asyncio.run(run())
    assert result['records'][0]['row']['company_name'] == 'Dynamic company'
    assert result['records'][0]['extraction_method'] == 'browser'


def test_html_parser_skips_empty_filter_row_before_real_headers():
    from app.connectors import parse_public_payload
    html='<html><table><tr><td><input type="search"></td></tr><tr><th>No</th><th>Маягтын нэр</th></tr><tr><td>1</td><td>Батлагдсан төсөв</td></tr></table></html>'.encode()
    fmt, rows = parse_public_payload(html, 'text/html', HOST+'/')
    assert fmt == 'html_table'
    assert rows == [{'table_index':1, 'No':'1', 'Маягтын нэр':'Батлагдсан төсөв'}]


def test_public_record_title_uses_content_not_hash_identifier():
    from app.record_search import record_title
    assert record_title({'raw':{'table_index':1, 'column_1':'1', 'column_2':'Батлагдсан төсөв'}}) == 'Батлагдсан төсөв'


def test_all_catalog_sources_have_keyless_public_acquisition():
    from app.connectors import connector_catalog, validate_public_url
    for source in connector_catalog():
        assert source['default_acquisition'] == 'public_web'
        assert source['upstream_key_required'] is False
        validate_public_url(source['public_web_url'])
    tender = next(s for s in connector_catalog() if s['code'] == 'tender_official')
    assert tender['public_web_url'] == 'https://www.tender.gov.mn/'
    assert '/api' not in tender['public_web_url']


def test_discovery_without_upstream_key_and_rejects_other_source_url(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from fastapi import HTTPException
    from app.db import Base
    from app.models import SourceRegistry
    from app.main import discover_web, WebDiscoveryIn
    calls = []
    async def crawl(url, *args):
        calls.append(url)
        return {'records': [], 'pages': [{'status': 'read', 'rows_read': 0}], 'status': 'bounded_complete'}
    monkeypatch.setattr('app.web_discovery.crawl_public_site', crawl)
    monkeypatch.delenv('TENDER_BEARER_TOKEN', raising=False)
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(SourceRegistry(code='tender_official', name='Tender', url='https://opendata.tender.gov.mn/api', access_method='official_api'))
        db.commit()
        result = asyncio.run(discover_web(WebDiscoveryIn(source_codes=['tender_official']), db))
        assert calls == ['https://www.tender.gov.mn/']
        assert result['sources'][0]['upstream_key_required'] is False
        assert db.query(SourceRegistry).first().last_success_at is not None
        try:
            asyncio.run(discover_web(WebDiscoveryIn(source_codes=['tender_official'], start_urls={'tender_official': 'https://iaac.mn/'}), db))
        except HTTPException as exc:
            assert exc.status_code == 422
        else:
            raise AssertionError('Cross-source URL accepted')
        assert len(calls) == 1
    engine.dispose()
