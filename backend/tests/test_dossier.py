from datetime import datetime,timezone,timedelta
import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app.db import Base
from app.models import SourceRegistry,SourceRecord,SearchObservation
from app.reporting import build_report
from app.search_observations import record_observation

@pytest.fixture
def db():
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as session:yield session
    engine.dispose()


def add(db,code,raw,identity='row',age=0):
    source=db.scalar(select(SourceRegistry).where(SourceRegistry.code==code))
    if not source:
        source=SourceRegistry(code=code,name=code,url='https://shilendans.gov.mn/',access_method='public')
        db.add(source);db.flush()
    record=SourceRecord(source_id=source.id,external_id=identity,raw_payload={'raw':raw},sha256=str(age).zfill(64),source_url='https://shilendans.gov.mn/organization/1',fetched_at=datetime.now(timezone.utc)-timedelta(days=age))
    db.add(record);db.commit()
    return record


def test_report_expands_across_sources_by_exact_registration_and_marks_claims(db):
    add(db,'shilendans',{'company_name':'Нэгдэл','registration':'1234567','parent_organization_name':'Толгой','state_share_percent':'100'})
    other=add(db,'company_registry',{'company_name':'Өөр бичлэг','registration':'1234567','organization_status':'Идэвхтэй'})
    report=build_report(db,q='Нэгдэл')
    assert report['summary']['records']==2
    assert next(r for r in report['records'] if r['id']==other.id)['match_basis']=='exact_registration'
    subject=report['dossier']['subjects'][0]
    assert subject['identity_status']=='cross_source_id'
    assert subject['legal_status_verified'] is False
    assert subject['facts']['organization_status']['claims'][0]['value']=='Идэвхтэй'
    assert subject['relationships'][0]['kind']=='declared_parent'
    assert subject['relationships'][0]['target_registration'] is None
    assert not report['owner_mentions']


def test_same_names_different_registrations_are_separate_subjects(db):
    add(db,'shilendans',{'company_name':'Ижил нэр','registration':'1234567'},identity='a')
    add(db,'shilendans',{'company_name':'Ижил нэр','registration':'7654321'},identity='b')
    data=build_report(db,q='Ижил нэр')['dossier']
    assert data['counts']['subjects']==2
    assert {s['registration'] for s in data['subjects']}=={'1234567','7654321'}
    assert all(len(s['record_ids'])==1 for s in data['subjects'])


def test_discrepancies_and_staleness_preserve_both_sources(db):
    add(db,'shilendans',{'company_name':'Тест','registration':'1234567','state_share_percent':'100'},age=45)
    add(db,'company_registry',{'company_name':'Тест','registration':'1234567','state_share_percent':'80'})
    data=build_report(db,registration='1234567')['dossier']
    subject=data['subjects'][0]
    assert len(subject['stale_records'])==1
    assert set(subject['conflicts'][0]['values'])=={'100','80'}
    assert data['counts']['conflicts']==1
    assert len(subject['facts']['state_share_percent']['claims'])==2


def test_report_categories_do_not_turn_profile_or_budget_into_transactions(db):
    add(db,'shilendans',{'company_name':'Тест','registration':'1234567','parent_organization_name':'Толгой','budget':'1000000','available_report_names':['Гүйлгээ']})
    checks={c['code']:c for c in build_report(db,q='Тест')['dossier']['checks']}
    assert checks['identity']['status']=='source_evidence'
    assert checks['parent']['status']=='source_evidence'
    assert checks['ownership']['status']=='no_structured_evidence'
    assert checks['transactions']['status']=='no_structured_evidence'
    assert checks['licenses']['status']=='adapter_missing'


def test_query_observations_are_scoped_and_cache_does_not_refresh_fetch_date(db):
    result={'source_code':'audit','status':'robots_unavailable','records':[],'pages':[],'note':'Unavailable'}
    record_observation(db,'  ТЕСТ ',result)
    first=db.scalar(select(SearchObservation)).observed_at
    record_observation(db,'тест',{**result,'cached':True})
    assert len(db.scalars(select(SearchObservation)).all())==1
    assert db.scalar(select(SearchObservation)).observed_at==first
    data=build_report(db,q='тест')['dossier']
    assert next(s for s in data['sources'] if s['code']=='audit')['status']=='unavailable'
    other=build_report(db,q='Өөр асуулт')['dossier']
    assert all(s['status']=='not_searched' for s in other['sources'])


def test_missing_registrations_and_multiple_ids_are_not_merged(db):
    add(db,'shilendans',{'text':'Тест баримт','rows':[{'registration':'1234567'},{'registration':'7654321'}]})
    data=build_report(db,q='Тест')['dossier']
    assert not data['subjects']
    assert len(data['unresolved_documents'])==1


def test_no_result_does_not_assert_legal_status_or_innocence(db):
    data=build_report(db,registration='1234567')['dossier']
    assert data['subjects'][0]['identity_status']=='not_found'
    assert not data['subjects'][0]['legal_status_verified']
    assert all(c['status']!='source_evidence' for c in data['checks'])


def test_structured_category_identifiers_have_source_record_proof(db):
    record=add(db,'courts',{'company_name':'Тест','registration':'1234567','decision_id':'CASE-1'})
    checks={c['code']:c for c in build_report(db,registration='1234567')['dossier']['checks']}
    assert checks['court']['status']=='source_evidence'
    assert checks['court']['evidence_record_ids']==[record.id]
    assert checks['transactions']['status']=='no_structured_evidence'


def test_all_failed_pages_are_unavailable_not_a_clean_negative_search(db):
    record_observation(db,'Тест',{'source_code':'tender_official','status':'partial','records':[], 'pages':[{'url':'https://www.tender.gov.mn/','status':'failed','error':'HTTPStatusError'}]})
    data=build_report(db,q='Тест')['dossier']
    source=next(s for s in data['sources'] if s['code']=='tender_official')
    assert source['status']=='unavailable'
    assert source['pages_checked']==1
