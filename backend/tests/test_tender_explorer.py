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
