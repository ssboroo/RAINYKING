import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.db import Base, configure_sqlite, get_db
from app.main import app
from app.models import SourceRegistry, SourceRecord, Tender


@pytest.fixture
def client():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    event.listen(engine, 'connect', configure_sqlite)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = SourceRegistry(code='test', name='Test', url='https://example.com', access_method='public')
        db.add(source)
        db.flush()
        record = SourceRecord(source_id=source.id, external_id='test', raw_payload={}, sha256='a'*64, source_url='https://example.com/evidence')
        db.add(record)
        db.flush()
        for i in range(35):
            db.add(Tender(external_id=f'T-{i}', title='ТөРиЙн тендер' if i == 0 else f'Tender {i}', budget_amount=100_000_000 if i == 0 else (None if i == 1 else 50_000_000), bid_count=1 if i == 0 else (None if i == 1 else 3), source_record_id=record.id))
        db.add(Tender(external_id='literal', title='100%_complete', source_record_id=record.id))
        db.commit()
    def override():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_pages_cover_entire_database_with_provenance(client):
    first = client.get('/api/v1/tender-explorer').json()
    second = client.get('/api/v1/tender-explorer?page=2').json()
    assert first['total'] == 36 and len(first['items']) == 30 and first['has_next']
    assert len(second['items']) == 6 and not second['has_next']
    assert not ({x['id'] for x in first['items']} & {x['id'] for x in second['items']})
    assert second['items'][-1]['source_url'] == 'https://example.com/evidence'
    assert client.get('/api/v1/tender-explorer?page=3').json()['items'] == []


def test_filters_find_records_beyond_first_page_and_include_budget_boundary(client):
    result = client.get('/api/v1/tender-explorer', params={'q': 'төрийн', 'budget': 'large', 'competition': 'single'}).json()
    assert result['total'] == 1
    assert result['items'][0]['external_id'] == 'T-0'
    small = client.get('/api/v1/tender-explorer?budget=small&competition=multiple').json()
    assert small['total'] == 33
    assert client.get('/api/v1/tender-explorer?competition=unknown').json()['total'] == 2
    assert client.get('/api/v1/tender-explorer?budget=known').json()['total'] == 34


def test_query_wildcards_are_literal(client):
    assert client.get('/api/v1/tender-explorer', params={'q': '%_'}).json()['total'] == 1
    assert client.get('/api/v1/tender-explorer', params={'q': 'absent'}).json()['total'] == 0


@pytest.mark.parametrize('query', ['page=0', 'page_size=101', 'budget=invalid', 'competition=invalid', 'q='+'a'*201])
def test_invalid_filters_are_rejected(client, query):
    assert client.get('/api/v1/tender-explorer?'+query).status_code == 422


def test_legacy_list_contract_remains_an_array(client):
    assert isinstance(client.get('/api/v1/tenders').json(), list)


def test_analysis_reports_coverage_without_treating_missing_values_as_zero(client):
    result = client.get('/api/v1/procurement-analysis').json()
    coverage = result['coverage']
    assert coverage['tenders'] == 36
    assert coverage['known_bid_count'] == 34
    assert coverage['single_bid_count'] == 1
    assert coverage['single_bid_share_pct'] == 2.9
    assert coverage['known_budget_count'] == 34
    assert coverage['budget_total'] == 1_750_000_000
    assert result['companies'] == []
    assert result['organizations'] == []


def test_analysis_uses_exact_entity_links_and_excludes_unregistered_company(client):
    from app.models import Company, Organization
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        org = Organization(name='Test organization', official_id='ORG1')
        registered = Company(name='Same name', registration='REG1')
        unresolved = Company(name='Same name')
        db.add_all([org, registered, unresolved])
        db.flush()
        tenders = db.query(Tender).order_by(Tender.id).limit(3).all()
        for t in tenders:
            t.organization_id = org.id
        tenders[0].awardee_company_id = registered.id
        tenders[1].awardee_company_id = registered.id
        tenders[2].awardee_company_id = unresolved.id
        db.commit()
    result = client.get('/api/v1/procurement-analysis').json()
    assert result['coverage']['linked_awardee_count'] == 3
    assert len(result['companies']) == 1
    assert result['companies'][0]['tender_count'] == 2
    assert result['companies'][0]['share_of_linked_tenders_pct'] == 66.7
    assert result['organizations'][0]['tender_count'] == 3
    assert result['organizations'][0]['single_bid_count'] == 1


def test_empty_analysis_has_no_invented_money_or_percentages(client):
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        db.query(Tender).delete()
        db.commit()
    coverage = client.get('/api/v1/procurement-analysis').json()['coverage']
    assert coverage['tenders'] == 0
    assert coverage['budget_total'] is None
    assert coverage['single_bid_share_pct'] is None


def test_record_search_and_owner_graph_keep_source_claims_distinct(client):
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        record = db.query(SourceRecord).first()
        record.raw_payload = {'company_name': 'Тест ХХК', 'registration': 'AB123', 'owners': [{'surname': 'Болд', 'first_name': 'Сарнай'}]}
        record_id = record.id
        db.commit()
    result = client.get('/api/v1/record-search', params={'owner_name': 'сарнай болд', 'registration': 'ab123'}).json()
    assert result['total'] == 1
    assert result['items'][0]['sha256'] == 'a'*64
    assert client.get('/api/v1/record-search?registration=AB').json()['total'] == 0
    assert client.get('/api/v1/record-search?source_code=missing').json()['total'] == 0
    graph = client.get(f'/api/v1/investigation/entity/source_record/{record_id}?depth=1').json()
    assert any(node['type'] == 'owner_mention' and node['label'] == 'Болд Сарнай' for node in graph['nodes'])
    assert any(edge['verification_status'] == 'source_claim' for edge in graph['edges'])


def test_unified_report_requires_a_subject_and_empty_results_are_not_exoneration(client):
    assert client.get('/api/v1/unified-report').status_code == 422
    assert client.get('/api/v1/unified-report?q=%20%20').status_code == 422
    result = client.get('/api/v1/unified-report?q=missing').json()
    assert result['summary']['records'] == 0
    assert result['summary']['budget_by_currency'] == {}
    assert any('зөрчил байхгүй' in text for text in result['conclusions'])
    assert result['source_coverage'][0]['imported_records'] == 1


def test_unified_report_deduplicates_versions_and_never_joins_by_owner_name(client):
    from datetime import datetime, timezone, timedelta
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        record = db.query(SourceRecord).first()
        record.raw_payload = {'company_name': 'Тест ХХК', 'registration': 'AB123', 'owner_name': 'Болд Сарнай'}
        record.fetched_at = datetime.now(timezone.utc)
        db.add(SourceRecord(source_id=record.source_id, external_id=record.external_id, raw_payload={'owner_name': 'Хуучин эзэмшигч'}, sha256='b'*64, source_url=record.source_url, fetched_at=record.fetched_at-timedelta(days=1)))
        db.commit()
    result = client.get('/api/v1/unified-report', params={'registration': 'ab123'}).json()
    assert result['summary']['records'] == 1
    assert result['summary']['excluded_older_versions'] == 1
    assert result['owner_mentions'][0]['status'] == 'source_claim'
    assert len(result['owner_mentions']) == 1
    assert result['records'][0]['sha256'] == 'a'*64
    assert result['summary']['linked_tenders'] == 36
    assert result['summary']['budget_by_currency'] == {'MNT': 1_750_000_000}
    assert client.get('/api/v1/unified-report?q=Хуучин').json()['summary']['records'] == 0


def test_unified_report_excludes_rejected_signals_and_keeps_proof(client):
    from app.models import Evidence, RiskFinding
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        record = db.query(SourceRecord).first()
        record.raw_payload = {'company_name': 'Тест ХХК'}
        evidence = Evidence(source_record_id=record.id, source_url=record.source_url, document_hash=record.sha256, excerpt='test')
        db.add(evidence)
        db.flush()
        tender = db.query(Tender).first()
        for code, status in [('R01', 'pending_review'), ('R03', 'rejected')]:
            db.add(RiskFinding(rule_code=code, title='Review signal', entity_type='tender', entity_id=tender.id, confidence=.5, explanation='Review only', limitations='Not proof', evidence_id=evidence.id, review_status=status))
        db.commit()
    result = client.get('/api/v1/unified-report?q=Тест').json()
    assert result['summary']['review_signals'] == 1
    assert len(result['dismissed_signals']) == 1
    assert result['signals'][0]['source_url'] == 'https://example.com/evidence'


def test_crawl_import_requires_admin_and_preserves_source_evidence(client, monkeypatch):
    from app.config import settings, Settings
    from app import web_discovery
    assert Settings(database_url='postgres://u:p@db:5432/app').database_url == 'postgresql+psycopg://u:p@db:5432/app'
    monkeypatch.setattr(settings, 'admin_api_key', 'test-admin-key')
    override = app.dependency_overrides[get_db]
    with next(override()) as db:
        source = db.query(SourceRegistry).first()
        source.url = 'https://opendata.gov.mn/'
        db.commit()
    async def mock_crawl(*args):
        return {'status':'bounded_complete', 'pages':[{'url':'https://opendata.gov.mn/test.csv','status':'read','rows_read':1}], 'records':[{'url':'https://opendata.gov.mn/test.csv','format':'csv','row_index':0,'row':{'company_name':'Crawl company','registration':'CRAWL123'},'content_sha256':'c'*64}]}
    monkeypatch.setattr(web_discovery, 'crawl_public_site', mock_crawl)
    payload = {'source_codes':['test'], 'q':'Crawl'}
    assert client.post('/api/v1/admin/sources/discover-web', json=payload).status_code == 403
    response = client.post('/api/v1/admin/sources/discover-web', json=payload, headers={'x-api-key':'test-admin-key'})
    assert response.status_code == 200
    assert response.json()['sources'][0]['records_saved'] == 1
    result = client.get('/api/v1/unified-report?registration=CRAWL123').json()
    assert result['summary']['records'] == 1
    assert result['records'][0]['source_url'] == 'https://opendata.gov.mn/test.csv'


def test_global_search_finds_decoded_mongolian_json_records(client):
    from app.db import get_db
    db = next(app.dependency_overrides[get_db]())
    source = db.query(SourceRegistry).first()
    db.add(SourceRecord(source_id=source.id,external_id='unicode-json',raw_payload={'raw':{'company_name':'МОНГОЛ ТӨСӨВ ХХК','registration':'MN123'}},sha256='b'*64,source_url='https://example.com/unicode'))
    db.commit()
    response=client.get('/api/v1/search',params={'q':'төсөв'})
    assert response.status_code==200
    assert any(r['external_id']=='unicode-json' for r in response.json()['source_records'])
    assert not client.get('/api/v1/search',params={'q':'%'}).json()['source_records']
    db.close()
