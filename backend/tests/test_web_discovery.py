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
