import asyncio
from fastapi.testclient import TestClient
from app.main import app
from app import live_search


def test_public_search_needs_no_admin_and_caches_identical_reads(monkeypatch):
    live_search._CACHE.clear()
    calls=[]
    async def crawl(url, query, **limits):
        calls.append((url,query,limits))
        return {'status':'partial','pages':[{'url':url,'status':'read'}], 'records':[{'url':url,'format':'html_table','row_index':0,'row':{'company_name':'Тест ХХК','registration':'AB123'},'extraction_method':'http','content_sha256':'a'*64}]}
    monkeypatch.setattr(live_search,'crawl_public_site',crawl)
    client=TestClient(app)
    a=client.get('/api/v1/public-web-search',params={'source_code':'tender_official','q':'Тест'})
    b=client.get('/api/v1/public-web-search',params={'source_code':'tender_official','q':'Тест'})
    assert a.status_code == 200
    assert a.json()['records'][0]['title']=='Тест ХХК'
    assert b.json()['cached'] is True
    assert len(calls)==1
    assert calls[0][2]=={'max_pages':3,'max_depth':2}
    assert client.get('/api/v1/public-web-search',params={'source_code':'unregistered','q':'Тест'}).status_code==422
    assert client.get('/api/v1/public-web-search',params={'source_code':'courts','q':'  '}).status_code==422
    assert client.post('/api/v1/admin/sources/discover-web',json={'source_codes':['courts']}).status_code==403
    live_search._CACHE.clear()


def test_parallel_identical_queries_share_one_crawl(monkeypatch):
    live_search._CACHE.clear()
    calls=[]
    async def crawl(*args,**kwargs):
        calls.append(1)
        await asyncio.sleep(.01)
        return {'status':'robots_denied','pages':[], 'records':[]}
    monkeypatch.setattr(live_search,'crawl_public_site',crawl)
    async def run():
        a,b=await asyncio.gather(live_search.search_source('courts','AB123'),live_search.search_source('courts','AB123'))
        assert a['status']==b['status']=='robots_denied'
        assert not a['records']
    asyncio.run(run())
    assert len(calls)==1
    live_search._CACHE.clear()


def test_search_form_recognizes_mongolian_placeholder_and_preserves_query():
    from app.web_discovery import discover_links
    raw='<form action="/find?category=law"><input name="textValue" placeholder="Хайх үгээ оруулна уу"><input type="hidden" name="lang" value="mn"></form>'.encode()
    links=discover_links(raw,'https://legalinfo.mn/','legalinfo.mn','төсөв')
    assert len(links)==1
    from urllib.parse import urlsplit,parse_qs
    assert parse_qs(urlsplit(links[0]).query)=={'category':['law'],'textValue':['төсөв'],'lang':['mn']}
