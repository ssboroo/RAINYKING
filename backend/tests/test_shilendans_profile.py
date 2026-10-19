import asyncio
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app.db import Base
from app.models import SourceRegistry,SourceRecord
from app.shilendans_browser import profile_fields
from app.profile_cache import cache_profiles
from app.reporting import build_report


def test_profile_fields_keep_official_id_and_parent_separate_from_ownership():
    data=profile_fields('Эрдэнэт үйлдвэр ТӨҮГ','https://shilendans.gov.mn/organization/38753',
        'Байгууллагын төрөл: Төрийн өмчит үйлдвэрийн газар\nБайгууллагын регистрийн дугаар: 2074192\nТолгой байгууллага: Эрдэнэсмонгол\nТөрд ногдох хувь хөрөнгө: 100\nНягтлан бодогч: Хувийн дугаар', ['Санхүүгийн тайлан'])
    assert data['registration']=='2074192'
    assert data['parent_organization_name']=='Эрдэнэсмонгол'
    assert data['state_share_percent']=='100'
    assert 'owner_name' not in data
    assert 'Хувийн дугаар' not in str(data)


def test_profile_read_through_cache_reaches_search_and_report_without_duplicates():
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(SourceRegistry(code='shilendans',name='Шилэн данс',url='https://shilendans.gov.mn/openData',access_method='public_web'))
        db.commit()
        row=profile_fields('Эрдэнэт үйлдвэр ТӨҮГ','https://shilendans.gov.mn/organization/38753', 'Байгууллагын регистрийн дугаар: 2074192\nТолгой байгууллага: Эрдэнэсмонгол',[])
        record={'url':'https://shilendans.gov.mn/organization/38753?ry=2026&group=5','format':'organization_profile','row':row,'content_sha256':'a'*64}
        assert cache_profiles(db,[record])==1
        assert cache_profiles(db,[record])==1
        assert len(db.scalars(select(SourceRecord)).all())==1
        report=build_report(db,registration='2074192')
        assert report['summary']['records']==1
        assert report['records'][0]['title']=='Эрдэнэт үйлдвэр ТӨҮГ'
        assert report['summary']['linked_tenders']==0
        assert not report['owner_mentions']
        assert cache_profiles(db,[{**record,'url':'https://evil.test/organization/38753'}])==0
    engine.dispose()


def test_live_shilendans_uses_search_adapter_not_homepage(monkeypatch):
    from app import live_search
    live_search._CACHE.clear()
    calls=[]
    async def adapter(query):
        calls.append(query)
        return {'status':'bounded_complete','records':[],'pages':[]}
    async def generic(*args,**kwargs):
        raise AssertionError('Homepage crawl must not replace organization search')
    monkeypatch.setattr('app.shilendans_browser.search_organizations',adapter)
    monkeypatch.setattr(live_search,'crawl_public_site',generic)
    result=asyncio.run(live_search.search_source('shilendans','Эрдэнэт үйлдвэр'))
    assert result['status']=='bounded_complete'
    assert calls==['Эрдэнэт үйлдвэр']
    live_search._CACHE.clear()
