from contextlib import asynccontextmanager
import hashlib, json, re
from difflib import SequenceMatcher
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, or_, func, cast, Text, case
from sqlalchemy.orm import Session
from .config import settings
from .db import Base, engine, get_db
from .models import SourceRegistry, SourceRecord, Company, Organization, Tender, TenderPlan, TenderSupplier, Evidence, RiskFinding, ReviewAction, AuditLog, Relationship, SourceSyncRun
from .services import ingest_tender, evaluate_r01, evaluate_risk_signals
from .connectors import connector_catalog, tender_invitations, tender_plans, tender_suppliers, ckan_package_search, ckan_package_show, fetch_public_resource, inspect_public_source, fetch_registered_public_resource, validate_public_url, ConnectorError
from .integrations import upsert_connector_registry, catalog_status, mark_result
from .normalization import normalize_record

SOURCE_SEEDS = [
    ('tender_official','Тендерийн Open Data API','https://opendata.tender.gov.mn/','bearer_api'),
    ('opendata_legal','Хуулийн этгээдийн нээлттэй өгөгдөл','https://opendata.gov.mn/dataview/4390','verify_access'),
    ('shilendans','Шилэн данс','https://shilendans.gov.mn/openData','verify_access'),
    ('audit','Төрийн аудит','https://open.audit.mn/report','verify_access'),
    ('iaac','АТГ','https://iaac.mn/','verify_access'),
    ('courts','Шүүхийн шийдвэр','https://shuukh.mn/','verify_access'),
]
@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    from .db import SessionLocal
    with SessionLocal() as db:
        for code,name,url,method in SOURCE_SEEDS:
            if not db.scalar(select(SourceRegistry).where(SourceRegistry.code == code)):
                db.add(SourceRegistry(code=code,name=name,url=url,access_method=method,license_notes='Зөвшөөрөл ба автомат таталтын нөхцөлийг баталгаажуулах шаардлагатай'))
        db.commit()
        upsert_connector_registry(db)
    yield

app=FastAPI(title='RAINY TRANSPARENCY API', version='0.1.0', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins.split(','),allow_methods=['GET','POST'],allow_headers=['*'])

class TenderIn(BaseModel):
    external_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_url: str
    organization_name: str|None = None
    organization_official_id: str|None = None
    company_name: str|None = None
    company_registration: str|None = None
    budget_amount: float|None = Field(default=None, ge=0)
    bid_count: int|None = Field(default=None, ge=0)
    procurement_method: str|None = None
    published_at: str|None = None

class ReviewIn(BaseModel):
    decision: str
    note: str = Field(min_length=5, max_length=5000)
    reviewer: str = Field(min_length=2, max_length=100)

def require_admin(x_api_key: str|None=Header(None)):
    if not settings.admin_api_key or x_api_key != settings.admin_api_key:
        raise HTTPException(403, 'Admin API key required')

@app.get('/health')
def health(): return {'ok':True,'service':'rainy-transparency'}

@app.get('/api/v1/source-status')
def source_status(db:Session=Depends(get_db)):
    upsert_connector_registry(db)
    return catalog_status(db)

@app.get('/api/v1/stats')
def stats(db:Session=Depends(get_db)):
    return {'tenders':db.scalar(select(func.count()).select_from(Tender)), 'plans':db.scalar(select(func.count()).select_from(TenderPlan)), 'suppliers':db.scalar(select(func.count()).select_from(TenderSupplier)), 'companies':db.scalar(select(func.count()).select_from(Company)), 'organizations':db.scalar(select(func.count()).select_from(Organization)), 'risk_findings':db.scalar(select(func.count()).select_from(RiskFinding))}

def store_source_payload(db:Session, external_id:str, payload:dict, source_url:str='https://www.tender.gov.mn/', source_code:str='tender_official')->SourceRecord:
    source=db.scalar(select(SourceRegistry).where(SourceRegistry.code==source_code))
    if source is None:
        raise ValueError('tender_official source is not registered')
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    digest=hashlib.sha256(encoded).hexdigest()
    record=db.scalar(select(SourceRecord).where(SourceRecord.source_id==source.id,SourceRecord.external_id==external_id,SourceRecord.sha256==digest))
    if record is None:
        record=SourceRecord(source_id=source.id,external_id=external_id,raw_payload=payload,sha256=digest,source_url=source_url)
        db.add(record);db.flush()
    from datetime import datetime, timezone
    source.last_success_at=datetime.now(timezone.utc);source.last_error=None
    return record

@app.get('/api/v1/search')
def search(q:str=Query(min_length=1,max_length=200),db:Session=Depends(get_db)):
    term=f'%{q.strip()}%'
    tenders=[tender_dict(x,db) for x in db.scalars(select(Tender).where(or_(Tender.title.ilike(term),Tender.external_id.ilike(term))).order_by(Tender.id.desc()).limit(30))]
    companies=[{'id':x.id,'name':x.name,'registration':x.registration,'resolution_status':x.resolution_status,'entity_type':'company'} for x in db.scalars(select(Company).where(or_(Company.name.ilike(term),Company.registration.ilike(term))).limit(30))]
    organizations=[{'id':x.id,'name':x.name,'official_id':x.official_id,'entity_type':'organization'} for x in db.scalars(select(Organization).where(or_(Organization.name.ilike(term),Organization.official_id.ilike(term))).limit(30))]
    suppliers=[{'id':x.id,'name':x.supplier_name,'registration':x.supplier_register,'invitation_id':x.invitation_id,'offered_price':x.offered_price,'status':x.status,'source_record_id':x.source_record_id,'source_url':(db.get(SourceRecord,x.source_record_id).source_url if db.get(SourceRecord,x.source_record_id) else None),'entity_type':'supplier'} for x in db.scalars(select(TenderSupplier).where(or_(TenderSupplier.supplier_name.ilike(term),TenderSupplier.supplier_register.ilike(term))).limit(30))]
    plans=[{'id':x.id,'name':x.title,'external_id':x.external_id,'tender_code':x.tender_code,'tender_year':x.tender_year,'organization_name':x.organization_name,'budget_amount':x.budget_amount,'entity_type':'tender_plan'} for x in db.scalars(select(TenderPlan).where(or_(TenderPlan.title.ilike(term),TenderPlan.external_id.ilike(term),TenderPlan.tender_code.ilike(term),TenderPlan.organization_name.ilike(term))).limit(30))]
    # Search public-source payloads as evidence records; never promote a name-only match into a verified person/company identity.
    source_matches=[]
    matched_records=db.scalars(select(SourceRecord).where(cast(SourceRecord.raw_payload,Text).ilike(term)).order_by(SourceRecord.fetched_at.desc()).limit(30)).all()
    for rec in matched_records:
        serialized=json.dumps(rec.raw_payload,ensure_ascii=False,sort_keys=True)
        source=db.get(SourceRegistry,rec.source_id)
        folded=serialized.casefold();needle=q.strip().casefold();position=folded.find(needle)
        source_matches.append({'id':rec.id,'source_name':source.name if source else rec.source_id,'external_id':rec.external_id,'source_url':rec.source_url,'sha256':rec.sha256,'fetched_at':rec.fetched_at,'match_excerpt':serialized[:420] if len(serialized)<=420 else serialized[max(0,position-150):position+270]})
    # Approximate name search is a discovery aid only. Never auto-link or auto-merge entities on name similarity.
    name_candidates=[]
    normalized_query=normalized_entity_name(q)
    if len(normalized_query)>=4:
        candidates=[]
        for entity_type, rows, name_key, id_key, identifier_key in [
            ('company', db.scalars(select(Company).order_by(Company.id.desc()).limit(1500)).all(), 'name', 'id', 'registration'),
            ('organization', db.scalars(select(Organization).order_by(Organization.id.desc()).limit(1500)).all(), 'name', 'id', 'official_id'),
            ('supplier', db.scalars(select(TenderSupplier).order_by(TenderSupplier.id.desc()).limit(2000)).all(), 'supplier_name', 'id', 'supplier_register'),
            ('tender_plan', db.scalars(select(TenderPlan).order_by(TenderPlan.id.desc()).limit(1000)).all(), 'title', 'id', 'tender_code'),
        ]:
            for obj in rows:
                label=getattr(obj,name_key,None) or ''
                normalized_label=normalized_entity_name(label)
                if not normalized_label or normalized_label==normalized_query:
                    continue
                # Blend whole-string similarity with token overlap so short name fragments still surface.
                similarity=SequenceMatcher(None,normalized_query,normalized_label).ratio()
                q_tokens=set(normalized_query.split()); n_tokens=set(normalized_label.split())
                overlap=len(q_tokens & n_tokens)/max(1,len(q_tokens))
                score=max(similarity,overlap*0.82)
                if score>=0.62:
                    candidates.append({'entity_type':entity_type,'id':getattr(obj,id_key),'name':label,'identifier':getattr(obj,identifier_key,None),'similarity':round(min(score,1.0),3),'match_kind':'approximate_name_only','investigation_allowed':entity_type in ('company','organization','supplier','tender_plan')})
        # Also scan recent source documents using their normalized name fields so
        # public registry rows can be found before they are resolved into a Company.
        recent_records=db.scalars(select(SourceRecord).order_by(SourceRecord.fetched_at.desc()).limit(1500)).all()
        for record in recent_records:
            payload=record.raw_payload if isinstance(record.raw_payload,dict) else {}
            raw=payload.get('raw') if isinstance(payload.get('raw'),dict) else payload
            normalized=payload.get('normalized') if isinstance(payload.get('normalized'),dict) else normalize_record(raw if isinstance(raw,dict) else {})
            label=normalized.get('name') or normalized.get('organization') or normalized.get('title')
            if not label:
                continue
            normalized_label=normalized_entity_name(str(label))
            if not normalized_label or normalized_label==normalized_query:
                continue
            similarity=SequenceMatcher(None,normalized_query,normalized_label).ratio()
            q_tokens=set(normalized_query.split()); n_tokens=set(normalized_label.split())
            overlap=len(q_tokens & n_tokens)/max(1,len(q_tokens))
            score=max(similarity,overlap*0.82)
            if score>=0.62:
                source=db.get(SourceRegistry,record.source_id)
                candidates.append({'entity_type':'source_record','id':record.id,'name':str(label),'identifier':record.external_id,'similarity':round(min(score,1.0),3),'match_kind':'source_document_name_only','source_name':source.name if source else None,'investigation_allowed':True})
        candidates.sort(key=lambda x:(x['similarity'],bool(x['identifier'])),reverse=True)
        seen=set()
        for candidate in candidates:
            key=(candidate['entity_type'],candidate['id'])
            if key in seen: continue
            seen.add(key); name_candidates.append(candidate)
            if len(name_candidates)>=25: break
    return {'query':q,'tenders':tenders,'companies':companies,'organizations':organizations,'suppliers':suppliers,'tender_plans':plans,'source_records':source_matches,'name_candidates':name_candidates,'note':'Нэрээр таарсан түүхий эх сурвалж болон ойролцоо нэрийн үр дүн нь зөвхөн хайлтын сэжүүр. Нэрийн төстэй байдал дангаараа ижил этгээд, хамаарал эсвэл авлигыг нотлохгүй. Холбоосыг регистр, албан ID, эх баримтаар тусад нь баталгаажуулна.'}


def tender_dict(t,db):
    rec = db.get(SourceRecord,t.source_record_id)
    return {'id':t.id,'external_id':t.external_id,'title':t.title,'organization_id':t.organization_id,'awardee_company_id':t.awardee_company_id,'budget_amount':t.budget_amount,'currency':t.currency,'bid_count':t.bid_count,'procurement_method':t.procurement_method,'published_at':t.published_at,'source_url':rec.source_url if rec else None,'fetched_at':rec.fetched_at if rec else None}

@app.get('/api/v1/tender-plans')
def tender_plan_list(limit:int=Query(default=30,ge=1,le=100),db:Session=Depends(get_db)):
    out=[]
    for x in db.scalars(select(TenderPlan).order_by(TenderPlan.id.desc()).limit(limit)):
        rec=db.get(SourceRecord,x.source_record_id)
        out.append({'id':x.id,'external_id':x.external_id,'title':x.title,'tender_code':x.tender_code,'tender_year':x.tender_year,'budget_amount':x.budget_amount,'organization_name':x.organization_name,'source_record_id':x.source_record_id,'source_url':rec.source_url if rec else None})
    return out

@app.get('/api/v1/tenders')
def tenders(limit:int=Query(default=30,ge=1,le=100),db:Session=Depends(get_db)):
    return [tender_dict(t,db) for t in db.scalars(select(Tender).order_by(Tender.id.desc()).limit(limit))]

class WebDiscoveryIn(BaseModel):
    source_codes: list[str] = Field(min_length=1, max_length=12)
    q: str = Field(default='', max_length=200)
    max_pages: int = Field(default=5, ge=1, le=10)
    max_depth: int = Field(default=2, ge=0, le=3)

@app.post('/api/v1/admin/sources/discover-web', dependencies=[Depends(require_admin)])
async def discover_web(data: WebDiscoveryIn, db: Session = Depends(get_db)):
    from .web_discovery import crawl_public_site
    from datetime import datetime, timezone
    sources = []
    for code in dict.fromkeys(data.source_codes):
        source = db.scalar(select(SourceRegistry).where(SourceRegistry.code == code))
        if source is None: raise HTTPException(404, 'Эх сурвалж бүртгэлгүй: ' + code)
        try: validate_public_url(source.url)
        except ConnectorError as exc: raise HTTPException(422, str(exc))
        sources.append(source)
    results = []
    for source in sources:
        run = SourceSyncRun(source_code=source.code, source_url=source.url, status='running')
        db.add(run); db.commit(); db.refresh(run)
        try:
            start_url = next((item['docs'] for item in connector_catalog() if item['code'] == source.code), source.url)
            result = await crawl_public_site(start_url, data.q, data.max_pages, data.max_depth)
            saved = 0
            for record in result.pop('records'):
                identity = hashlib.sha256((record['url']+'#'+str(record['row_index'])).encode()).hexdigest()
                store_source_payload(db, 'web:'+identity, {'kind': 'public_web_document', 'format': record['format'],
                                     'raw': record['row'], 'normalized': normalize_record(record['row']),
                                     'content_sha256': record['content_sha256'], 'extraction_method': record.get('extraction_method', 'http')}, source_url=record['url'], source_code=source.code)
                saved += 1
            run.status = 'success' if any(p['status'] == 'read' for p in result['pages']) else 'failed'
            source.access_notes = 'Веб таталтын хүрээ: '+result['status']+'; '+', '.join(sorted({p['status'] for p in result['pages']}))
            if result['status'] != 'bounded_complete': run.error_message = source.access_notes
            run.records_saved = saved; run.records_seen = sum(p.get('rows_read', 0) for p in result['pages'])
            run.finished_at = datetime.now(timezone.utc)
            if run.status == 'failed': run.error_message = result['status']
            db.commit()
            results.append({'source_code': source.code, 'run_id': run.id, 'records_saved': saved, **result})
        except Exception as exc:
            db.rollback(); run = db.get(SourceSyncRun, run.id)
            run.status = 'failed'; run.finished_at = datetime.now(timezone.utc); run.error_message = type(exc).__name__
            db.commit()
            results.append({'source_code': source.code, 'status': 'failed', 'error': type(exc).__name__, 'records_saved': 0})
    return {'sources': results, 'note': 'Импортолсон баримт одоо нэгдсэн хайлт ба тайланд орно. Crawl-ийн хүрээ, саатал, алдааг эх сурвалж тус бүрээр харуулна.'}

@app.get('/api/v1/unified-report')
def unified_report(q: str = Query(default='', max_length=200),
                   registration: str = Query(default='', max_length=100),
                   db: Session = Depends(get_db)):
    if not q.strip() and not registration.strip():
        raise HTTPException(422, 'Нэр, түлхүүр үг эсвэл регистр оруулна уу.')
    from .reporting import build_report
    return build_report(db, q.strip(), registration.strip())

@app.get('/api/v1/record-search')
def record_search(
    q: str = Query(default='', max_length=200),
    registration: str = Query(default='', max_length=100),
    company_name: str = Query(default='', max_length=200),
    organization_name: str = Query(default='', max_length=200),
    owner_name: str = Query(default='', max_length=200),
    source_code: str = Query(default='', max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    from .record_search import matches_record, record_title
    query = select(SourceRecord, SourceRegistry).join(SourceRegistry, SourceRecord.source_id == SourceRegistry.id)
    if source_code.strip(): query = query.where(SourceRegistry.code == source_code.strip())
    items = []; total = 0; scanned = 0
    # Stream the entire selected source set: do not silently restrict discovery to recent rows.
    for record, source in db.execute(query.order_by(SourceRecord.id.desc()).execution_options(yield_per=200)):
        scanned += 1
        match = matches_record(record.raw_payload, q=q, registration=registration.strip(),
                               company_name=company_name, organization_name=organization_name, owner_name=owner_name)
        if match is None: continue
        if offset <= total < offset + limit:
            items.append({'id': record.id, 'external_id': record.external_id,
                          'source_code': source.code, 'source_name': source.name, 'title': record_title(record.raw_payload, f'Эх баримт #{record.id}'),
                          'source_url': record.source_url, 'sha256': record.sha256,
                          'fetched_at': record.fetched_at, **match,
                          'match_status': 'source_claim_requires_review'})
        total += 1
    return {'items': items, 'total': total, 'offset': offset, 'limit': limit,
            'has_next': offset + limit < total, 'scanned_records': scanned,
            'note': 'Регистрийг яг тааруулна; бусад талбарын үгсийг нэг талбарын утгад хайна. Эзэмшигчийн нэр нь эх баримтын мэдэгдэл бөгөөд ижил хүн эсвэл хүчинтэй эзэмшлийг дангаараа нотлохгүй. Бүх шүүлт AND нөхцөлтэй. Зөвхөн импортолсон мэдээллийн хамрах хүрээнд хайна.'}

@app.get('/api/v1/procurement-analysis')
def procurement_analysis(db: Session = Depends(get_db)):
    """Describe imported procurement data; budget is never actual expenditure."""
    total, known_bids, single_bids, known_budgets, budget_total, linked_awardees = db.execute(
        select(func.count(Tender.id), func.count(Tender.bid_count),
               func.sum(case((Tender.bid_count == 1, 1), else_=0)),
               func.count(Tender.budget_amount), func.sum(Tender.budget_amount),
               func.count(Tender.awardee_company_id))
    ).one()
    organizations = []
    leaders = db.execute(
        select(Organization.id, Organization.name, Organization.official_id,
               func.count(Tender.id).label('tender_count'),
               func.sum(Tender.budget_amount).label('budget_total'),
               func.sum(case((Tender.bid_count == 1, 1), else_=0)).label('single_bid_count'))
        .join(Tender, Tender.organization_id == Organization.id)
        .group_by(Organization.id, Organization.name, Organization.official_id)
        .order_by(func.count(Tender.id).desc(), Organization.id).limit(20)
    ).all()
    for row in leaders:
        organizations.append(dict(row._mapping))
    companies = []
    leaders = db.execute(
        select(Company.id, Company.name, Company.registration,
               func.count(Tender.id).label('tender_count'),
               func.sum(Tender.budget_amount).label('linked_tender_budget'))
        .join(Tender, Tender.awardee_company_id == Company.id)
        .where(Company.registration.is_not(None), Company.registration != '')
        .group_by(Company.id, Company.name, Company.registration)
        .order_by(func.count(Tender.id).desc(), Company.id).limit(20)
    ).all()
    for row in leaders:
        item = dict(row._mapping)
        item['share_of_linked_tenders_pct'] = round(row.tender_count / linked_awardees * 100, 1) if linked_awardees else None
        companies.append(item)
    return {
        'coverage': {'tenders': total, 'known_bid_count': known_bids,
                     'single_bid_count': single_bids or 0,
                     'single_bid_share_pct': round((single_bids or 0) / known_bids * 100, 1) if known_bids else None,
                     'known_budget_count': known_budgets, 'budget_total': budget_total,
                     'linked_awardee_count': linked_awardees},
        'organizations': organizations, 'companies': companies,
        'methodology': 'Зөвхөн импортолсон тендерийн сан. Нэг саналын хувийг саналын тоо тодорхой тендерээс тооцно. Компанийн хувийг шалгарсан компанийн холбоостой тендерээс тооцно; регистргүй компанийг жагсаалтад нэгтгэхгүй. Төсөв нь бодит төлбөр, гэрээний дүн эсвэл компанийн орлого биш. Төвлөрөл нь зөрчлийн нотолгоо биш.',
        'limitations': 'Төсвийн гүйлгээ, гэрээний гүйцэтгэл, аудит, өмчлөл, ХОМ болон улс төрийн санхүүжилтийг энэ шинжилгээ баталгаажуулаагүй. Сангийн хамрах хүрээнээс гадуурх мэдээллийг дүгнэхгүй.'
    }

@app.get('/api/v1/tender-explorer')
def tender_explorer(
    q: str = Query(default='', max_length=200),
    budget: Literal['any', 'known', 'large', 'small'] = 'any',
    competition: Literal['any', 'multiple', 'single', 'unknown'] = 'any',
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    filters = []
    if q.strip():
        # Treat wildcard characters as literal query text.
        escaped = q.strip().casefold().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        filters.append(or_(*[
            column.ilike(f'%{escaped}%', escape='\\')
            for column in (Tender.title, Tender.external_id)
        ]))
    if budget == 'known': filters.append(Tender.budget_amount.is_not(None))
    elif budget == 'large': filters.append(Tender.budget_amount >= 100_000_000)
    elif budget == 'small': filters.append(Tender.budget_amount < 100_000_000)
    if competition == 'multiple': filters.append(Tender.bid_count >= 2)
    elif competition == 'single': filters.append(Tender.bid_count == 1)
    elif competition == 'unknown': filters.append(Tender.bid_count.is_(None))
    total = db.scalar(select(func.count()).select_from(Tender).where(*filters)) or 0
    rows = db.scalars(select(Tender).where(*filters).order_by(Tender.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {'items': [tender_dict(t, db) for t in rows], 'total': total,
            'page': page, 'page_size': page_size, 'has_next': page * page_size < total}

@app.get('/api/v1/tenders/{tid}')
def tender(tid:int,db:Session=Depends(get_db)):
    obj=db.get(Tender,tid)
    if not obj:raise HTTPException(404,'Tender not found')
    return tender_dict(obj,db)

@app.get('/api/v1/companies/{cid}')
def company(cid:int,db:Session=Depends(get_db)):
    x=db.get(Company,cid)
    if not x:raise HTTPException(404,'Company not found')
    return {'id':x.id,'name':x.name,'registration':x.registration,'resolution_status':x.resolution_status}

@app.get('/api/v1/organizations/{oid}')
def organization(oid:int,db:Session=Depends(get_db)):
    x=db.get(Organization,oid)
    if not x:raise HTTPException(404,'Organization not found')
    return {'id':x.id,'name':x.name,'official_id':x.official_id}

@app.get('/api/v1/companies/{cid}/tenders')
def company_tenders(cid:int,db:Session=Depends(get_db)):
    return [tender_dict(x,db) for x in db.scalars(select(Tender).where(Tender.awardee_company_id==cid))]

@app.get('/api/v1/entities/{eid}/relationships')
def entity_relationships(eid:int,db:Session=Depends(get_db)):
    return [{'id':x.id,'from_type':x.from_type,'from_id':x.from_id,'to_type':x.to_type,'to_id':x.to_id,'relationship_type':x.relationship_type,'confidence':x.confidence,'verification_status':x.verification_status,'source_record_id':x.source_record_id} for x in db.scalars(select(Relationship).where(or_(Relationship.from_id==eid,Relationship.to_id==eid)))]

@app.get('/api/v1/graph')
def graph(entity_id:int,db:Session=Depends(get_db)):
    edges=entity_relationships(entity_id,db)
    return {'entity_id':entity_id,'edges':edges,'note':'Баталгаажаагүй холбоог гэм буруугийн нотолгоо гэж үзэхгүй.'}

@app.get('/api/v1/investigation/entity/{entity_type}/{entity_id}')
def investigation_entity_graph(entity_type:str,entity_id:int,depth:int=Query(default=2,ge=1,le=3),db:Session=Depends(get_db)):
    allowed={'company','organization','tender','supplier','tender_plan','source_record'}
    if entity_type not in allowed: raise HTTPException(422,'entity_type must be company, organization, tender, supplier, or tender_plan')
    def node(t,i):
        obj=None; label=''; detail={}; source_id=None
        if t=='company':
            obj=db.get(Company,i)
            if obj: label=obj.name; detail={'registration':obj.registration,'resolution_status':obj.resolution_status}; source_id=obj.source_record_id
        elif t=='organization':
            obj=db.get(Organization,i)
            if obj: label=obj.name; detail={'official_id':obj.official_id}
        elif t=='tender':
            obj=db.get(Tender,i)
            if obj: label=obj.title; detail={'external_id':obj.external_id,'budget_amount':obj.budget_amount,'bid_count':obj.bid_count,'published_at':obj.published_at}; source_id=obj.source_record_id
        elif t=='supplier':
            obj=db.get(TenderSupplier,i)
            if obj: label=obj.supplier_name; detail={'registration':obj.supplier_register,'invitation_id':obj.invitation_id,'offered_price':obj.offered_price,'status':obj.status}; source_id=obj.source_record_id
        elif t=='tender_plan':
            obj=db.get(TenderPlan,i)
            if obj: label=obj.title; detail={'external_id':obj.external_id,'tender_code':obj.tender_code,'year':obj.tender_year,'budget_amount':obj.budget_amount}; source_id=obj.source_record_id
        elif t=='source_record':
            obj=db.get(SourceRecord,i)
            if obj:
                source=db.get(SourceRegistry,obj.source_id)
                from .record_search import record_title
                label=record_title(obj.raw_payload, (source.name if source else 'Эх баримт')+' #'+str(obj.id))
                detail={'external_id':obj.external_id,'fetched_at':obj.fetched_at.isoformat() if obj.fetched_at else None}
                source_id=obj.id
        if not obj: return None
        rec=db.get(SourceRecord,source_id) if source_id else None
        return {'key':f'{t}:{i}','type':t,'id':i,'label':label,'details':detail,'source_record_id':source_id,'source_url':rec.source_url if rec else None,'sha256':rec.sha256 if rec else None}
    from .record_search import record_fields, record_title
    start=node(entity_type,entity_id)
    if not start: raise HTTPException(404,'Entity not found')
    nodes={start['key']:start}; edges=[]; frontier=[(entity_type,entity_id,0)]; seen={(entity_type,entity_id)}
    def add_edge(a,b,kind,confidence,status,source_id=None,note=None):
        if not a or not b or a['key']==b['key']: return
        key=(a['key'],b['key'],kind)
        if any((e['from'],e['to'],e['type'])==key for e in edges): return
        edges.append({'from':a['key'],'to':b['key'],'type':kind,'confidence':confidence,'verification_status':status,'source_record_id':source_id,'note':note})
        nodes[a['key']]=a;nodes[b['key']]=b
    while frontier:
        current_type,current_id,level=frontier.pop(0)
        if level>=depth: continue
        neighbors=[]
        for rel in db.scalars(select(Relationship).where(or_(
            (Relationship.from_type==current_type)&(Relationship.from_id==current_id),
            (Relationship.to_type==current_type)&(Relationship.to_id==current_id)
        ), Relationship.verification_status != 'rejected').limit(250)).all():
            if rel.from_type==current_type and rel.from_id==current_id: other=(rel.to_type,rel.to_id); direction=(current_type,current_id,rel.to_type,rel.to_id)
            else: other=(rel.from_type,rel.from_id); direction=(rel.from_type,rel.from_id,current_type,current_id)
            a=node(direction[0],direction[1]); b=node(direction[2],direction[3])
            if a and b: add_edge(a,b,rel.relationship_type,rel.confidence,rel.verification_status,rel.source_record_id,rel.reviewer_note)
            neighbors.append(other)
        if current_type=='tender':
            t=db.get(Tender,current_id)
            if t:
                if t.organization_id:
                    org=node('organization',t.organization_id); add_edge(start if False else node('tender',t.id),org,'захиалагч байгууллага',1.0,'structural',t.source_record_id,'Tender.organization_id холбоос')
                    neighbors.append(('organization',t.organization_id))
                if t.awardee_company_id:
                    comp=node('company',t.awardee_company_id); add_edge(node('tender',t.id),comp,'шалгарсан нийлүүлэгч',1.0,'structural',t.source_record_id,'Tender.awardee_company_id холбоос')
                    neighbors.append(('company',t.awardee_company_id))
                rec=db.get(SourceRecord,t.source_record_id); raw=rec.raw_payload if rec and isinstance(rec.raw_payload,dict) else {}
                inv=str(raw.get('upstream_invitation_id') or '')
                if inv:
                    for supplier in db.scalars(select(TenderSupplier).where(TenderSupplier.invitation_id==inv).limit(100)).all():
                        sn=node('supplier',supplier.id); add_edge(node('tender',t.id),sn,'тендерийн оролцогч',1.0,'source_linked',supplier.source_record_id,'Албан API-ийн invitation ID таарсан'); neighbors.append(('supplier',supplier.id))
        elif current_type=='organization':
            for t in db.scalars(select(Tender).where(Tender.organization_id==current_id).limit(100)).all():
                add_edge(node('tender',t.id),node('organization',current_id),'захиалагч байгууллага',1.0,'structural',t.source_record_id,'Tender.organization_id холбоос'); neighbors.append(('tender',t.id))
        elif current_type=='company':
            comp=db.get(Company,current_id)
            for t in db.scalars(select(Tender).where(Tender.awardee_company_id==current_id).limit(100)).all():
                add_edge(node('tender',t.id),node('company',current_id),'шалгарсан нийлүүлэгч',1.0,'structural',t.source_record_id,'Tender.awardee_company_id холбоос'); neighbors.append(('tender',t.id))
            if comp and comp.registration:
                for s in db.scalars(select(TenderSupplier).where(TenderSupplier.supplier_register==comp.registration).limit(100)).all():
                    add_edge(node('company',current_id),node('supplier',s.id),'регистрийн дугаар ижил',1.0,'identifier_match',s.source_record_id,'Регистрийн дугаараар таарсан'); neighbors.append(('supplier',s.id))
            if comp and comp.registration:
                target = ''.join(comp.registration.split()).casefold()
                for record in db.scalars(select(SourceRecord).execution_options(yield_per=200)):
                    fields, _ = record_fields(record.raw_payload)
                    if any(''.join(value.split()).casefold() == target for value in fields['registration']):
                        add_edge(node('company',current_id),node('source_record',record.id),'регистрийн дугаар яг таарсан',1.0,'identifier_match',record.id,'Компанийн регистр эх баримтад яг таарсан; эзэмшигчийн нэрийг тусдаа эх сурвалжийн мэдэгдэл гэж үзнэ')
                        neighbors.append(('source_record',record.id))
        elif current_type=='supplier':
            s=db.get(TenderSupplier,current_id)
            if s:
                for t in db.scalars(select(Tender).limit(1000)).all():
                    rec=db.get(SourceRecord,t.source_record_id); raw=rec.raw_payload if rec and isinstance(rec.raw_payload,dict) else {}
                    if str(raw.get('upstream_invitation_id') or '')==s.invitation_id:
                        add_edge(node('tender',t.id),node('supplier',s.id),'тендерийн оролцогч',1.0,'source_linked',s.source_record_id,'Албан API-ийн invitation ID таарсан'); neighbors.append(('tender',t.id))
                if s.supplier_register:
                    for c in db.scalars(select(Company).where(Company.registration==s.supplier_register).limit(30)).all():
                        add_edge(node('company',c.id),node('supplier',s.id),'регистрийн дугаар ижил',1.0,'identifier_match',s.source_record_id,'Регистрийн дугаараар таарсан'); neighbors.append(('company',c.id))
        elif current_type=='tender_plan':
            plan=db.get(TenderPlan,current_id)
            if plan:
                for t in db.scalars(select(Tender).where(Tender.external_id==plan.tender_code).limit(30)).all() if plan.tender_code else []:
                    add_edge(node('tender_plan',plan.id),node('tender',t.id),'тендерийн код ижил',1.0,'identifier_match',plan.source_record_id,'Тендерийн код яг таарсан'); neighbors.append(('tender',t.id))
        elif current_type=='source_record':
            current=db.get(SourceRecord,current_id)
            if current:
                owner_fields, _ = record_fields(current.raw_payload)
                for index, owner_name in enumerate(owner_fields['owner_name']):
                    mention = {'key': f'owner_mention:{current.id}:{index}', 'type': 'owner_mention',
                               'id': current.id, 'label': owner_name,
                               'details': {'identity_status': 'unresolved_source_claim'},
                               'source_record_id': current.id, 'source_url': current.source_url, 'sha256': current.sha256}
                    add_edge(node('source_record',current.id),mention,'эх баримтад дурдсан эзэмшигч',0.5,'source_claim',current.id,'Эх сурвалжийн эзэмшлийн мэдэгдэл; нэр ижил хүнийг нэгтгээгүй, хүчинтэй эзэмшлийг тусад нь нягтална')
                def flatten(obj):
                    values={}
                    if isinstance(obj,dict):
                        for k,v in obj.items():
                            if isinstance(v,dict): values.update(flatten(v))
                            elif isinstance(v,list):
                                for item in v:
                                    if isinstance(item,dict): values.update(flatten(item))
                            elif v is not None: values[k]=v
                    return values
                current_fields=normalize_record(flatten(current.raw_payload if isinstance(current.raw_payload,dict) else {}))
                registration_values = owner_fields['registration']
                registration = re.sub(r'\s+', '', registration_values[0]).upper() if len(registration_values) == 1 else ''
                tender_code=str(current_fields.get('tender_id') or '').strip()
                contract_code=str(current_fields.get('contract_id') or '').strip()
                if registration:
                    for c in db.scalars(select(Company).where(Company.registration==registration).limit(50)).all():
                        add_edge(node('source_record',current.id),node('company',c.id),'регистрийн дугаар яг таарсан',1.0,'identifier_match',current.id,'Эх өгөгдөлд байгаа регистрийн дугаарыг яг тааруулсан'); neighbors.append(('company',c.id))
                    for s in db.scalars(select(TenderSupplier).where(TenderSupplier.supplier_register==registration).limit(100)).all():
                        add_edge(node('source_record',current.id),node('supplier',s.id),'регистрийн дугаар яг таарсан',1.0,'identifier_match',current.id,'Эх өгөгдөлд байгаа регистрийн дугаарыг яг тааруулсан'); neighbors.append(('supplier',s.id))
                if tender_code:
                    for t in db.scalars(select(Tender).where(Tender.external_id==tender_code).limit(50)).all():
                        add_edge(node('source_record',current.id),node('tender',t.id),'тендерийн дугаар яг таарсан',1.0,'identifier_match',current.id,'Тендерийн дугаарыг яг тааруулсан'); neighbors.append(('tender',t.id))
                    for p in db.scalars(select(TenderPlan).where(or_(TenderPlan.external_id==tender_code,TenderPlan.tender_code==tender_code)).limit(50)).all():
                        add_edge(node('source_record',current.id),node('tender_plan',p.id),'тендерийн код яг таарсан',1.0,'identifier_match',current.id,'Тендерийн кодыг яг тааруулсан'); neighbors.append(('tender_plan',p.id))
                if contract_code:
                    # Keep exact contract identifiers available as cross-source evidence, without guessing an entity from a name.
                    pass
                if registration or tender_code or contract_code:
                    for other in db.scalars(select(SourceRecord).where(SourceRecord.id!=current.id).order_by(SourceRecord.fetched_at.desc()).limit(1000)).all():
                        raw=other.raw_payload if isinstance(other.raw_payload,dict) else {}
                        fields=normalize_record(flatten(raw))
                        other_fields, _ = record_fields(raw)
                        same_reg = bool(registration and any(re.sub(r'\s+', '', value).upper() == registration for value in other_fields['registration']))
                        same_tender=bool(tender_code and str(fields.get('tender_id') or '').strip()==tender_code)
                        same_contract=bool(contract_code and str(fields.get('contract_id') or '').strip()==contract_code)
                        if not (same_reg or same_tender or same_contract): continue
                        match='регистрийн дугаар' if same_reg else ('тендерийн код' if same_tender else 'гэрээний дугаар')
                        other_node=node('source_record',other.id)
                        add_edge(node('source_record',current.id),other_node,match+' яг таарсан',1.0,'identifier_match',current.id,'Хоёр эх баримтын стандартчилсан албан ID яг ижил'); neighbors.append(('source_record',other.id))
        for other in neighbors:
            if other not in seen and node(*other):
                seen.add(other);frontier.append((other[0],other[1],level+1))
    risk_rows=[{'id':r.id,'rule_code':r.rule_code,'title':r.title,'severity':r.severity,'confidence':r.confidence,'explanation':r.explanation,'limitations':r.limitations,'evidence_id':r.evidence_id,'review_status':r.review_status} for r in db.scalars(select(RiskFinding).where(RiskFinding.entity_type==entity_type,RiskFinding.entity_id==entity_id).limit(50))]
    return {'root':start,'depth':depth,'nodes':list(nodes.values()),'edges':edges,'risks':risk_rows,'note':'Граф нь гүн болон салааллын хязгаартай хайгуулын харагдац; бүх холбоог бүрэн хамарсан дүгнэлт биш. Энэ граф нь өгөгдөлд байгаа холбоосыг харуулна. Нэр төстэй байдал дангаараа холбоо тогтоохгүй; эрсдэлийн дохио нь авлига, гэм бурууг нотлохгүй.'}


@app.get('/api/v1/risks')
def risks(db:Session=Depends(get_db)):
    return [{'id':x.id,'rule_code':x.rule_code,'title':x.title,'entity_type':x.entity_type,'entity_id':x.entity_id,'severity':x.severity,'confidence':x.confidence,'explanation':x.explanation,'limitations':x.limitations,'evidence_id':x.evidence_id,'review_status':x.review_status} for x in db.scalars(select(RiskFinding).order_by(RiskFinding.id.desc()).limit(100))]

@app.get('/api/v1/evidence/{eid}')
def evidence(eid:int,db:Session=Depends(get_db)):
    x=db.get(Evidence,eid)
    if not x:raise HTTPException(404,'Evidence not found')
    return {'id':x.id,'source_url':x.source_url,'document_hash':x.document_hash,'excerpt':x.excerpt,'page_number':x.page_number,'verified':x.verified}


class CrossSourceLinkBuildIn(BaseModel):
    max_records: int = Field(default=50000, ge=100, le=100000)

@app.post('/api/v1/admin/rebuild-cross-source-links',dependencies=[Depends(require_admin)])
def rebuild_cross_source_links(data:CrossSourceLinkBuildIn,db:Session=Depends(get_db)):
    def flatten(obj):
        values={}
        if isinstance(obj,dict):
            for key,value in obj.items():
                if isinstance(value,dict): values.update(flatten(value))
                elif isinstance(value,list):
                    for item in value:
                        if isinstance(item,dict): values.update(flatten(item))
                elif value is not None: values[key]=value
        return values
    records=db.scalars(select(SourceRecord).order_by(SourceRecord.id.asc()).limit(data.max_records)).all()
    groups={}
    for rec in records:
        payload=rec.raw_payload if isinstance(rec.raw_payload,dict) else {}
        fields=normalize_record(flatten(payload))
        identifiers=[
            ('registration',str(fields.get('registration') or '').strip().upper()),
            ('tender_id',str(fields.get('tender_id') or '').strip()),
            ('contract_id',str(fields.get('contract_id') or '').strip()),
        ]
        for kind,value in identifiers:
            if not value or (kind=='registration' and len(value)<5) or (kind!='registration' and len(value)<3): continue
            groups.setdefault((kind,value),[]).append(rec)
    created=0; groups_linked=0; skipped_same_source=0
    for (kind,value),items in groups.items():
        # One anchor per identifier avoids quadratic edge growth; only cross-source links are persisted.
        unique={item.id:item for item in items}
        items=list(unique.values())
        if len({item.source_id for item in items})<2: continue
        anchor_record=items[0]; group_created=0
        for other in items[1:]:
            if other.source_id==anchor_record.source_id:
                skipped_same_source+=1
                continue
            exists=db.scalar(select(Relationship).where(
                Relationship.from_type=='source_record',Relationship.from_id==anchor_record.id,
                Relationship.to_type=='source_record',Relationship.to_id==other.id,
                Relationship.relationship_type==f'exact_{kind}'
            ))
            if exists: continue
            db.add(Relationship(from_type='source_record',from_id=anchor_record.id,to_type='source_record',to_id=other.id,relationship_type=f'exact_{kind}',source_record_id=other.id,confidence=1.0,verification_status='identifier_match',reviewer_note=f'Автомат холболт: {kind} яг ижил утгатай; value_hash={hashlib.sha256(value.encode()).hexdigest()[:16]}. Нэрийн төстэй байдлаар холбогоогүй.'))
            created+=1;group_created+=1
        if group_created: groups_linked+=1
        if created and created%500==0: db.flush()
    db.commit()
    return {'records_scanned':len(records),'max_records':data.max_records,'identifier_groups':len(groups),'groups_linked':groups_linked,'links_created':created,'same_source_candidates_skipped':skipped_same_source,'link_policy':'Exact registry/tender/contract identifiers only; no name-only automatic merge.','note':'max_records хязгаарт хүрсэн бол дахин ажиллуулж үлдсэн өгөгдлийг хамруулах шаардлагатай.'}

@app.post('/api/v1/admin/import/tender',dependencies=[Depends(require_admin)])
def import_tender(data:TenderIn,db:Session=Depends(get_db)):
    try: obj,changed=ingest_tender(db,data.model_dump(exclude_none=True)); db.commit()
    except ValueError as exc: db.rollback(); raise HTTPException(422,str(exc))
    return {'id':obj.id,'changed':changed}

@app.post('/api/v1/admin/evaluate',dependencies=[Depends(require_admin)])
def evaluate(db:Session=Depends(get_db)):
    counts=evaluate_risk_signals(db)
    db.commit()
    return {'new_findings':sum(counts.values()),'by_rule':counts,'implemented_rules':['R01','R02','R03'],'status':'review_required','note':'Эдгээр нь шалгах дохио; гэм буруугийн дүгнэлт биш.'}

@app.post('/api/v1/reviews/{rid}',dependencies=[Depends(require_admin)])
def review(rid:int, data:ReviewIn,db:Session=Depends(get_db)):
    if data.decision not in ('verified','dismissed','needs_more_evidence'):
        raise HTTPException(422,'Unknown decision')
    r=db.get(RiskFinding,rid)
    if not r:raise HTTPException(404,'Risk not found')
    r.review_status=data.decision
    db.add(ReviewAction(risk_id=rid,reviewer=data.reviewer,decision=data.decision,note=data.note))
    db.add(AuditLog(actor=data.reviewer,action='risk_review',details={'risk_id':rid,'decision':data.decision}))
    db.commit()
    return {'ok':True,'review_status':r.review_status}


@app.get('/api/v1/integrations')
def integrations(db:Session=Depends(get_db)):
    upsert_connector_registry(db)
    return catalog_status(db)

class SyncIn(BaseModel):
    year: int|None = Field(default=None, ge=2000, le=2100)
    max_pages: int = Field(default=1, ge=1, le=100)
    per_page: int = Field(default=100, ge=1, le=100)

class SupplierSyncIn(BaseModel):
    invitation_id: int = Field(gt=0)
    max_pages: int = Field(default=1, ge=1, le=100)
    per_page: int = Field(default=100, ge=1, le=100)

@app.post('/api/v1/admin/sync/opendata-catalog',dependencies=[Depends(require_admin)])
async def sync_opendata_catalog(q:str='*',max_pages:int=Query(default=1,ge=1,le=50),per_page:int=Query(default=100,ge=1,le=100),db:Session=Depends(get_db)):
    pages=0; received=0; saved=0
    try:
        for page in range(max_pages):
            result=await ckan_package_search(q=q,start=page*per_page,rows=per_page)
            pages+=1; datasets=result.get('results',[]); received+=len(datasets)
            for item in datasets:
                ext=str(item.get('id') or item.get('name') or '').strip()
                if not ext: continue
                rec=store_source_payload(db,'opendata:dataset:'+ext,{'kind':'dataset_catalog','dataset':item},source_url='https://opendata.gov.mn/dataset/'+str(item.get('name') or ext),source_code='government_opendata')
                saved+=1
            db.commit()
            if len(datasets)<per_page or (result.get('count') and (page+1)*per_page>=result['count']): break
        mark_result(db,'government_opendata')
    except ConnectorError as exc:
        db.rollback(); mark_result(db,'government_opendata',str(exc)); raise HTTPException(502,str(exc))
    return {'source':'government_opendata','pages_fetched':pages,'datasets_received':received,'catalog_records_saved':saved,'query':q,'note':'Каталогын metadata хадгалсан. CSV/JSON өгөгдлийг resource sync endpoint-оор тусад нь татна.'}

class OpenDataResourceSyncIn(BaseModel):
    dataset_id: str = Field(min_length=1,max_length=300)
    resource_id: str|None = Field(default=None,max_length=300)
    max_rows: int = Field(default=500,ge=1,le=500)
    max_bytes: int = Field(default=5000000,ge=1000,le=5000000)

@app.post('/api/v1/admin/sync/opendata-resource',dependencies=[Depends(require_admin)])
async def sync_opendata_resource(data:OpenDataResourceSyncIn,db:Session=Depends(get_db)):
    try:
        dataset=await ckan_package_show(data.dataset_id)
        resources=dataset.get('resources') or []
        resource=next((r for r in resources if data.resource_id and str(r.get('id'))==data.resource_id),None) if data.resource_id else next((r for r in resources if str(r.get('format','')).lower() in ('csv','json')),None)
        if not resource: raise HTTPException(404,'CSV/JSON resource олдсонгүй. resource_id эсвэл dataset_id-г шалгана уу.')
        url=str(resource.get('url') or '')
        rows=await fetch_public_resource(url,max_bytes=data.max_bytes,max_rows=data.max_rows)
        for index,row in enumerate(rows,1):
            encoded=json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'))
            digest=hashlib.sha256(encoded.encode()).hexdigest()
            store_source_payload(db,'opendata:row:'+str(resource.get('id'))+':'+str(index),{'kind':'dataset_row','dataset_id':data.dataset_id,'resource_id':resource.get('id'),'row_number':index,'row':row,'sha256':digest},source_url=url,source_code='government_opendata')
        db.commit(); mark_result(db,'government_opendata')
        return {'source':'government_opendata','dataset_id':data.dataset_id,'dataset_title':dataset.get('title'),'resource_id':resource.get('id'),'resource_format':resource.get('format'),'source_url':url,'rows_fetched':len(rows),'records_stored':len(rows),'limit_rows':data.max_rows,'note':'Эхний хувилбар 5 MB хүртэл CSV/JSON resource-ээс дээд тал нь 500 мөр хадгална. Лицензийг шалгана.'}
    except HTTPException: raise
    except ConnectorError as exc:
        db.rollback(); mark_result(db,'government_opendata',str(exc)); raise HTTPException(502,str(exc))
    except Exception:
        db.rollback(); raise

@app.post('/api/v1/admin/sync/tenders',dependencies=[Depends(require_admin)])
async def sync_tenders(data:SyncIn,db:Session=Depends(get_db)):
    imported=0; pages=0; total=0
    try:
        for page in range(1,data.max_pages+1):
            body=await tender_invitations(page,data.per_page,data.year);pages+=1
            records=body.get('data',[]);total=int(body.get('total') or 0)
            for item in records:
                code=str(item.get('invitationNumber') or item.get('invitationId') or item.get('tenderCode') or '').strip()
                title=str(item.get('tenderName') or '').strip()
                if not code or not title:continue
                payload={'external_id':code,'title':title,'source_url':('https://www.tender.gov.mn/mn/embed/'+str(item.get('invitationId'))) if item.get('invitationId') else 'https://www.tender.gov.mn/','organization_name':item.get('budgetEntityName') or item.get('budgetGovernerName'),'organization_official_id':item.get('registrationNumber') or item.get('budgetEntityCode'),'budget_amount':safe_float(item.get('totalBudget')),'procurement_method':item.get('ruleName') or item.get('tenderTypeName'),'published_at':item.get('publishDate') or item.get('publishedDate'),'upstream_invitation_id':str(item.get('invitationId') or '').strip() or None}
                try:
                    _,changed=ingest_tender(db,payload);imported+=int(changed)
                except ValueError:continue
            db.commit()
            if not records or len(records)<data.per_page or (total and page*data.per_page>=total):break
        mark_result(db,'tender_official')
    except ConnectorError as exc:
        db.rollback();mark_result(db,'tender_official',str(exc));raise HTTPException(502,str(exc))
    return {'source':'tender_official','pages_fetched':pages,'records_total_reported':total,'records_imported_or_updated':imported,'year':data.year,'note':'API нь invitation мэдээлэл буцаадаг; supplier endpoint-ийн тоо баталгаажаагүй үед bid_count таамгаар бөглөхгүй.'}

@app.post('/api/v1/admin/sync/tender-plans',dependencies=[Depends(require_admin)])
async def sync_plans(data:SyncIn,db:Session=Depends(get_db)):
    pages=0;received=0;created=0;updated=0;total=0
    try:
        for page in range(1,data.max_pages+1):
            body=await tender_plans(page,data.per_page,data.year);pages+=1;records=body.get('data',[]);total=int(body.get('total') or 0);received+=len(records)
            for item in records:
                ext=str(item.get('tenderId') or item.get('invitationId') or item.get('tenderCode') or '').strip()
                title=str(item.get('tenderName') or '').strip()
                if not ext or not title: continue
                rec=store_source_payload(db,'plan:'+ext,item,source_url=('https://www.tender.gov.mn/mn/embed/'+str(item.get('tenderId') or item.get('invitationId'))) if (item.get('tenderId') or item.get('invitationId')) else 'https://www.tender.gov.mn/')
                row=db.scalar(select(TenderPlan).where(TenderPlan.external_id==ext))
                values={'title':title,'tender_code':str(item.get('tenderCode') or '').strip() or None,'tender_year':safe_int(item.get('tenderYear')),'budget_amount':safe_float(item.get('totalBudget') or item.get('yearBudget')),'organization_name':item.get('budgetEntityName') or item.get('budgetGovernerName'),'source_record_id':rec.id}
                if row:
                    for key,val in values.items(): setattr(row,key,val)
                    updated+=1
                else:
                    db.add(TenderPlan(external_id=ext,**values));created+=1
            db.commit()
            if not records or len(records)<data.per_page or (total and page*data.per_page>=total):break
        mark_result(db,'tender_official')
    except ConnectorError as exc:
        db.rollback();mark_result(db,'tender_official',str(exc));raise HTTPException(502,str(exc))
    return {'source':'tender_official','pages_fetched':pages,'records_received':received,'created':created,'updated':updated,'total_reported':total,'year':data.year,'stored':True}

@app.post('/api/v1/admin/sync/tender-suppliers',dependencies=[Depends(require_admin)])
async def sync_suppliers(data:SupplierSyncIn,db:Session=Depends(get_db)):
    pages=0;received=0;created=0;updated=0;total=0
    try:
        for page in range(1,data.max_pages+1):
            body=await tender_suppliers(data.invitation_id,page,data.per_page);pages+=1;records=body.get('data',[]);total=int(body.get('total') or 0);received+=len(records)
            for item in records:
                reg=str(item.get('supplierRegister') or '').strip() or None
                name=str(item.get('supplierName') or '').strip()
                if not name: continue
                invitation=str(item.get('invitationId') or data.invitation_id)
                external=f'{invitation}:{reg}' if reg else f'{invitation}:name:{hashlib.sha256(name.casefold().encode()).hexdigest()[:24]}'
                rec=store_source_payload(db,'supplier:'+external,item,source_url=('https://www.tender.gov.mn/mn/embed/'+str(invitation)))
                row=db.scalar(select(TenderSupplier).where(TenderSupplier.external_id==external))
                values={'invitation_id':invitation,'supplier_name':name,'supplier_register':reg,'offered_price':safe_float(item.get('openedBidderPrice')),'status':item.get('wfmStatusName'),'comment':item.get('commentText'),'source_record_id':rec.id}
                if row:
                    for key,val in values.items(): setattr(row,key,val)
                    updated+=1
                else:
                    db.add(TenderSupplier(external_id=external,**values));created+=1
            db.commit()
            if not records or len(records)<data.per_page or (total and page*data.per_page>=total):break
        mark_result(db,'tender_official')
    except ConnectorError as exc:
        db.rollback();mark_result(db,'tender_official',str(exc));raise HTTPException(502,str(exc))
    return {'source':'tender_official','invitation_id':data.invitation_id,'pages_fetched':pages,'records_received':received,'created':created,'updated':updated,'total_reported':total,'stored':True,'access_note':'Энэ endpoint зөвхөн админд нээлттэй. Нийтийн дэлгэцэд гаргахаас өмнө өгөгдөл ашиглах эрх, хувийн мэдээллийн шаардлагыг шалгана.'}

@app.post('/api/v1/admin/check/tender-suppliers',dependencies=[Depends(require_admin)])
async def check_suppliers(data:SupplierSyncIn):
    try:
        body=await tender_suppliers(data.invitation_id,1,data.per_page)
        return {'source':'tender_official','invitation_id':data.invitation_id,'total':body.get('total'),'sample_count':len(body.get('data',[])),'data':body.get('data',[]),'note':'Нийтэд харуулахын өмнө API эрх болон хувийн мэдээллийн шаардлагыг шалгана.'}
    except ConnectorError as exc:raise HTTPException(502,str(exc))

def safe_int(value):
    try: return int(value) if value not in (None,'') else None
    except (ValueError,TypeError): return None

def safe_float(value):
    if value is None or value=='':return None
    try:return float(str(value).replace(',',''))
    except (ValueError,TypeError):return None




class RelationshipCreateIn(BaseModel):
    from_type: str = Field(pattern="^(company|organization|tender)$")
    from_id: int = Field(gt=0)
    to_type: str = Field(pattern="^(company|organization|tender)$")
    to_id: int = Field(gt=0)
    relationship_type: str = Field(min_length=3,max_length=100)
    source_record_id: int = Field(gt=0)
    confidence: float = Field(default=0.5,ge=0,le=1)
    reviewer_note: str = Field(default="",max_length=2000)

class RelationshipReviewIn(BaseModel):
    verification_status: str = Field(pattern="^(verified|rejected|pending_review)$")
    reviewer_note: str = Field(min_length=3,max_length=2000)

def entity_exists(db, entity_type, entity_id):
    model={"company":Company,"organization":Organization,"tender":Tender}.get(entity_type)
    return bool(model and db.get(model,entity_id))

def normalized_entity_name(value):
    value=(value or "").casefold().strip()
    value=re.sub(r"[\W_]+"," ",value,flags=re.UNICODE)
    return re.sub(r"\s+"," ",value).strip()

@app.get('/api/v1/admin/source-records',dependencies=[Depends(require_admin)])
def source_records(source_code:str|None=None,q:str|None=None,limit:int=Query(default=50,ge=1,le=200),offset:int=Query(default=0,ge=0),db:Session=Depends(get_db)):
    stmt=select(SourceRecord).order_by(SourceRecord.id.desc())
    if source_code:
        source=db.scalar(select(SourceRegistry).where(SourceRegistry.code==source_code))
        if not source: raise HTTPException(404,'Эх сурвалж олдсонгүй')
        stmt=stmt.where(SourceRecord.source_id==source.id)
    records=list(db.scalars(stmt.offset(offset).limit(limit)))
    out=[]
    for record in records:
        payload=record.raw_payload if isinstance(record.raw_payload,dict) else {}
        normalized=payload.get('normalized',{}) if isinstance(payload,dict) else {}
        if q and q.casefold() not in json.dumps(payload,ensure_ascii=False).casefold():
            continue
        source=db.get(SourceRegistry,record.source_id)
        out.append({'id':record.id,'source_code':source.code if source else None,'external_id':record.external_id,'source_url':record.source_url,'sha256':record.sha256,'fetched_at':record.fetched_at,'parser_version':record.parser_version,'normalized':normalized,'raw_payload':payload})
    return {'items':out,'limit':limit,'offset':offset,'count':len(out)}

@app.get('/api/v1/admin/entity-resolution/candidates',dependencies=[Depends(require_admin)])
def entity_resolution_candidates(threshold:float=Query(default=0.88,ge=0.5,le=1),limit:int=Query(default=100,ge=1,le=500),db:Session=Depends(get_db)):
    candidates=[]
    companies=list(db.scalars(select(Company).order_by(Company.id)))
    for i,left in enumerate(companies):
        if left.registration: continue
        a=normalized_entity_name(left.name)
        if len(a)<4: continue
        for right in companies[i+1:]:
            if right.registration: continue
            b=normalized_entity_name(right.name)
            score=SequenceMatcher(None,a,b).ratio()
            if score>=threshold:
                candidates.append({'entity_type':'company','left':{'id':left.id,'name':left.name,'registration':left.registration,'status':left.resolution_status},'right':{'id':right.id,'name':right.name,'registration':right.registration,'status':right.resolution_status},'similarity':round(score,4),'recommendation':'manual_review_only'})
    organizations=list(db.scalars(select(Organization).order_by(Organization.id)))
    for i,left in enumerate(organizations):
        a=normalized_entity_name(left.name)
        if len(a)<4: continue
        for right in organizations[i+1:]:
            if left.official_id and right.official_id and left.official_id!=right.official_id: continue
            b=normalized_entity_name(right.name)
            score=SequenceMatcher(None,a,b).ratio()
            if score>=threshold:
                candidates.append({'entity_type':'organization','left':{'id':left.id,'name':left.name,'official_id':left.official_id},'right':{'id':right.id,'name':right.name,'official_id':right.official_id},'similarity':round(score,4),'recommendation':'manual_review_only'})
    candidates.sort(key=lambda x:x['similarity'],reverse=True)
    return {'threshold':threshold,'count':min(len(candidates),limit),'candidates':candidates[:limit],'warning':'Нэрийн төстэй байдал нь ижил этгээд болохыг нотлохгүй. Автомат нэгтгэл хийгээгүй.'}

@app.post('/api/v1/admin/relationships',dependencies=[Depends(require_admin)])
def create_relationship(data:RelationshipCreateIn,db:Session=Depends(get_db)):
    if data.from_type==data.to_type and data.from_id==data.to_id: raise HTTPException(422,'Өөртэй нь холбоос үүсгэх боломжгүй')
    if not entity_exists(db,data.from_type,data.from_id) or not entity_exists(db,data.to_type,data.to_id):
        raise HTTPException(404,'Холбоосын нэг талын entity олдсонгүй')
    evidence=db.get(SourceRecord,data.source_record_id)
    if not evidence: raise HTTPException(404,'Эх сурвалжийн бүртгэл олдсонгүй')
    existing=db.scalar(select(Relationship).where(Relationship.from_type==data.from_type,Relationship.from_id==data.from_id,Relationship.to_type==data.to_type,Relationship.to_id==data.to_id,Relationship.relationship_type==data.relationship_type,Relationship.source_record_id==data.source_record_id))
    if existing: return {'id':existing.id,'status':'already_exists'}
    row=Relationship(from_type=data.from_type,from_id=data.from_id,to_type=data.to_type,to_id=data.to_id,relationship_type=data.relationship_type,source_record_id=data.source_record_id,confidence=data.confidence,verification_status='pending_review',reviewer_note=data.reviewer_note)
    db.add(row);db.flush()
    db.add(AuditLog(actor='admin_api',action='relationship_created',details={'relationship_id':row.id,'from_type':row.from_type,'from_id':row.from_id,'to_type':row.to_type,'to_id':row.to_id,'source_record_id':row.source_record_id}))
    db.commit()
    return {'id':row.id,'verification_status':row.verification_status,'confidence':row.confidence}

@app.post('/api/v1/admin/relationships/{relationship_id}/review',dependencies=[Depends(require_admin)])
def review_relationship(relationship_id:int,data:RelationshipReviewIn,db:Session=Depends(get_db)):
    row=db.get(Relationship,relationship_id)
    if not row: raise HTTPException(404,'Холбоос олдсонгүй')
    row.verification_status=data.verification_status;row.reviewer_note=data.reviewer_note
    db.add(AuditLog(actor='admin_api',action='relationship_reviewed',details={'relationship_id':row.id,'verification_status':row.verification_status,'note':row.reviewer_note}))
    db.commit()
    return {'id':row.id,'verification_status':row.verification_status,'reviewer_note':row.reviewer_note}

@app.get('/api/v1/relationships')
def public_relationships(verification_status:str='verified',limit:int=Query(default=100,ge=1,le=500),db:Session=Depends(get_db)):
    if verification_status not in ('verified','pending_review','rejected'): raise HTTPException(422,'Invalid status')
    rows=db.scalars(select(Relationship).where(Relationship.verification_status==verification_status).order_by(Relationship.id.desc()).limit(limit))
    return [{'id':x.id,'from_type':x.from_type,'from_id':x.from_id,'to_type':x.to_type,'to_id':x.to_id,'relationship_type':x.relationship_type,'confidence':x.confidence,'verification_status':x.verification_status,'source_record_id':x.source_record_id,'reviewer_note':x.reviewer_note} for x in rows]


@app.get('/api/v1/admin/overview',dependencies=[Depends(require_admin)])
def admin_overview(db:Session=Depends(get_db)):
    """Single read model for the admin console; reports facts from persisted data only."""
    sources=source_metrics(db)
    runs=sync_runs(limit=20,db=db)
    pending_relationships=db.scalar(select(func.count()).select_from(Relationship).where(Relationship.verification_status=='pending_review')) or 0
    pending_risks=db.scalar(select(func.count()).select_from(RiskFinding).where(RiskFinding.review_status=='pending_review')) or 0
    pending_companies=db.scalar(select(func.count()).select_from(Company).where(Company.resolution_status=='pending_review')) or 0
    return {
        'database':'connected',
        'counts':{
            'source_records':db.scalar(select(func.count()).select_from(SourceRecord)) or 0,
            'tenders':db.scalar(select(func.count()).select_from(Tender)) or 0,
            'plans':db.scalar(select(func.count()).select_from(TenderPlan)) or 0,
            'suppliers':db.scalar(select(func.count()).select_from(TenderSupplier)) or 0,
            'companies':db.scalar(select(func.count()).select_from(Company)) or 0,
            'organizations':db.scalar(select(func.count()).select_from(Organization)) or 0,
            'risks':db.scalar(select(func.count()).select_from(RiskFinding)) or 0,
        },
        'review_queue':{'relationships':pending_relationships,'risks':pending_risks,'companies':pending_companies},
        'sources':sources,
        'recent_sync_runs':runs,
        'scheduler':{
            'configured_sources':len(json.loads(__import__('os').environ.get('AUTO_SYNC_SOURCES','[]') or '[]')) if __import__('os').environ.get('AUTO_SYNC_SOURCES','[]').strip().startswith('[') else None,
            'interval_minutes':__import__('os').environ.get('AUTO_SYNC_INTERVAL_MINUTES','360'),
            'note':'Энэ нь environment тохиргоог л харуулна; scheduler process ажиллаж байгааг дангаараа батлахгүй.'
        }
    }

@app.get('/api/v1/admin/production-check',dependencies=[Depends(require_admin)])
def production_check(db:Session=Depends(get_db)):
    """Lightweight deployment diagnostics. It does not claim that external sources are reachable."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError
    from datetime import datetime,timezone
    checks=[]
    try:
        db.execute(text('SELECT 1')).scalar_one()
        checks.append({'name':'database_connection','status':'pass','detail':'SELECT 1 амжилттай'})
        tables=('source_registry','source_records','source_sync_runs','tenders','tender_plans','tender_suppliers','companies','organizations','relationships','evidence_items','risk_findings')
        existing=set(__import__('sqlalchemy').inspect(engine).get_table_names())
        missing=[t for t in tables if t not in existing]
        checks.append({'name':'database_schema','status':'pass' if not missing else 'fail','detail':'Шаардлагатай хүснэгтүүд шалгасан','missing_tables':missing})
    except SQLAlchemyError as exc:
        checks.append({'name':'database_connection','status':'fail','detail':type(exc).__name__})
    sources=source_metrics(db)
    checks.append({'name':'source_registry','status':'pass' if sources else 'fail','detail':f'{len(sources)} эх сурвалж бүртгэлтэй'})
    checks.append({'name':'admin_api_key','status':'pass' if bool(settings.admin_api_key) else 'fail','detail':'Тохируулсан' if settings.admin_api_key else 'ADMIN_API_KEY тохируулаагүй'})
    checks.append({'name':'tender_api_token','status':'configured' if settings.tender_bearer_token else 'blocked','detail':'Токен тохируулсан' if settings.tender_bearer_token else 'TENDER_BEARER_TOKEN шаардлагатай; худалдан авах ажиллагааны байгууллагаас авах'})
    checks.append({'name':'scheduler_configuration','status':'configured' if __import__('os').environ.get('AUTO_SYNC_SOURCES','[]').strip() not in ('','[]') else 'not_configured','detail':'AUTO_SYNC_SOURCES environment тохиргоог шалгасан; процессын амьд байдлыг батлахгүй'})
    return {'checked_at':datetime.now(timezone.utc).isoformat(),'overall':'pass' if all(c['status'] in ('pass','configured') for c in checks) else 'needs_attention','checks':checks,'important':'Энэ endpoint нь deployment diagnostics. Эх сурвалжийн бодит таталтыг батлахын тулд source-specific sync/validation endpoint-ийг тус бүр ажиллуулна.'}

@app.post('/api/v1/admin/sources/validate-all',dependencies=[Depends(require_admin)])
async def validate_all_sources(db:Session=Depends(get_db)):
    """Validate each source according to its actual integration type; never equate a portal homepage with a dataset import."""
    from datetime import datetime,timezone
    import hashlib
    results=[]
    registered=list(db.scalars(select(SourceRegistry).order_by(SourceRegistry.code)).all())
    for source in registered:
        run=SourceSyncRun(source_code=source.code,source_url=source.url,status='running')
        db.add(run);db.commit();db.refresh(run)
        try:
            if source.code=='tender_official':
                if not settings.tender_bearer_token:
                    run.status='blocked';run.error_message='TENDER_BEARER_TOKEN тохируулаагүй';run.finished_at=datetime.now(timezone.utc)
                    source.last_error=run.error_message;db.commit()
                    results.append({'source_code':source.code,'url':source.url,'status':'blocked','detail':'Тендерийн API token шаардлагатай. API эрхийг албан ёсоор авсны дараа бодит endpoint шалгана.'})
                    continue
                body=await tender_invitations(page=1,per_page=1)
                payload=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
                data_rows=body.get('data',[])
                run.status='success';run.records_seen=len(data_rows);run.records_saved=0;run.content_type='application/json';run.bytes_downloaded=len(payload);run.content_sha256=hashlib.sha256(payload).hexdigest()
                run.finished_at=datetime.now(timezone.utc);source.last_success_at=run.finished_at;source.last_error=None;db.commit()
                results.append({'source_code':source.code,'url':source.url,'status':'success','format':'official_api_json','sample_records':len(data_rows),'bytes_downloaded':len(payload),'content_sha256':run.content_sha256,'detail':'Бодит тендер API endpoint шалгасан; өгөгдлийг импортлоогүй.'})
                continue
            if source.code=='government_opendata':
                package_result=await ckan_package_search(q='*',start=0,rows=1)
                payload=json.dumps(package_result,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
                datasets=package_result.get('results',[])
                run.status='success';run.records_seen=len(datasets);run.records_saved=0;run.content_type='application/json';run.bytes_downloaded=len(payload);run.content_sha256=hashlib.sha256(payload).hexdigest()
                run.finished_at=datetime.now(timezone.utc);source.last_success_at=run.finished_at;source.last_error=None;db.commit()
                results.append({'source_code':source.code,'url':'https://opendata.gov.mn/api/3/action/package_search?q=%2A&rows=1','status':'success','format':'ckan_api_json','sample_records':len(datasets),'bytes_downloaded':len(payload),'content_sha256':run.content_sha256,'detail':'CKAN-ийн бодит package_search API шалгасан; resource бүрийн таталт тусдаа баталгаажна.'})
                continue
            validate_public_url(source.url)
            fmt,rows,meta=await fetch_registered_public_resource(source.url,max_bytes=5_000_000,max_rows=3)
            run.status='success';run.records_seen=len(rows);run.records_saved=0
            run.bytes_downloaded=meta['bytes_downloaded'];run.content_type=meta['content_type'];run.content_sha256=meta['sha256'];run.finished_at=datetime.now(timezone.utc)
            source.last_success_at=run.finished_at;source.last_error=None;db.commit()
            # A homepage response is not evidence that the underlying dataset/API schema is usable.
            results.append({'source_code':source.code,'url':meta['final_url'],'status':'portal_reachable_schema_unverified','format':fmt,'sample_records':len(rows),'bytes_downloaded':meta['bytes_downloaded'],'content_sha256':meta['sha256'],'detail':'Порталын URL болон parser шалгасан. Өгөгдлийн API/schema эсвэл тодорхой файл баталгаажаагүй тул бодит dataset холболт гэж тооцохгүй.'})
        except Exception as exc:
            db.rollback()
            failed=db.get(SourceSyncRun,run.id)
            current=db.get(SourceRegistry,source.id)
            detail=(str(exc) or type(exc).__name__)[:2000]
            if failed:
                failed.status='failed';failed.error_message=detail;failed.finished_at=datetime.now(timezone.utc)
            if current:current.last_error=detail
            db.commit()
            results.append({'source_code':source.code,'url':source.url,'status':'failed','detail':detail[:500]})
    statuses=[r['status'] for r in results]
    return {'checked_at':datetime.now(timezone.utc).isoformat(),'checked_sources':len(results),'api_or_resource_success':sum(1 for s in statuses if s=='success'),'portal_only_schema_unverified':sum(1 for s in statuses if s=='portal_reachable_schema_unverified'),'failed':sum(1 for s in statuses if s=='failed'),'blocked':sum(1 for s in statuses if s=='blocked'),'results':results,'note':'success нь зөвхөн source-specific API эсвэл ресурс шалгалт амжилттайг илэрхийлнэ. portal_reachable_schema_unverified нь өгөгдөл таталт батлагдсан гэсэн үг биш.'}

def stable_record_identity(normalized, row):
    # Keep row identity scoped to a procurement/contract when those fields exist.
    tender = normalized.get("tender_id") or row.get("invitationId") or row.get("invitation_id") or row.get("Тендерийн дугаар")
    contract = normalized.get("contract_id") or row.get("contractId") or row.get("contract_id")
    registration = normalized.get("registration") or row.get("supplierRegister") or row.get("supplier_register")
    if tender or contract:
        identity = "|".join(str(v).casefold().strip() for v in (tender or "", contract or "", registration or "") if v)
    elif registration:
        identity = str(registration).casefold().strip()
    else:
        identity = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

class SourceInspectIn(BaseModel):
    source_code: str = Field(min_length=1,max_length=90)
    url: str = Field(min_length=10,max_length=2000)
class PublicSourceSyncIn(BaseModel):
    source_code: str = Field(min_length=1,max_length=90)
    url: str = Field(min_length=10,max_length=2000)
    max_rows: int = Field(default=500,ge=1,le=500)
    max_bytes: int = Field(default=5000000,ge=1000,le=5000000)

@app.post('/api/v1/admin/sources/inspect',dependencies=[Depends(require_admin)])
async def inspect_source(data:SourceInspectIn,db:Session=Depends(get_db)):
    source=db.scalar(select(SourceRegistry).where(SourceRegistry.code==data.source_code))
    if not source:raise HTTPException(404,'Эх сурвалж бүртгэлгүй байна.')
    try:
        host=validate_public_url(data.url); registered=validate_public_url(source.url)
        if host!=registered and not host.endswith('.'+registered):raise ConnectorError('URL бүртгэсэн эх сурвалжийн домэйнтэй таарахгүй.')
        return {'source_code':data.source_code,**(await inspect_public_source(data.url))}
    except ConnectorError as exc:
        mark_result(db,data.source_code,str(exc));raise HTTPException(502,str(exc))

@app.post('/api/v1/admin/sources/sync-public',dependencies=[Depends(require_admin)])
async def sync_public_source(data:PublicSourceSyncIn,db:Session=Depends(get_db)):
    source=db.scalar(select(SourceRegistry).where(SourceRegistry.code==data.source_code))
    if not source:raise HTTPException(404,'Эх сурвалж бүртгэлгүй байна.')
    try:
        host=validate_public_url(data.url); registered=validate_public_url(source.url)
        if host!=registered and not host.endswith('.'+registered):raise ConnectorError('URL бүртгэсэн эх сурвалжийн домэйнтэй таарахгүй.')
    except ConnectorError as exc:raise HTTPException(422,str(exc))
    run=SourceSyncRun(source_code=data.source_code,source_url=data.url,status='running');db.add(run);db.commit();db.refresh(run)
    try:
        fmt,rows,meta=await fetch_registered_public_resource(data.url,max_bytes=data.max_bytes,max_rows=data.max_rows)
        for index,row in enumerate(rows,1):
            norm=normalize_record(row)
            identity=norm.get('registration') or norm.get('tender_id') or norm.get('contract_id') or norm.get('title') or ''
            stable=stable_record_identity(norm,row)
            store_source_payload(db,'public:'+data.source_code+':'+stable,{'kind':'public_source_record','format':fmt,'row_number':index,'normalized':norm,'raw':row,'content_sha256':meta['sha256']},source_url=meta['final_url'],source_code=data.source_code)
        from datetime import datetime,timezone
        run.status='success';run.records_seen=len(rows);run.records_saved=len(rows);run.bytes_downloaded=meta['bytes_downloaded'];run.content_type=meta['content_type'];run.content_sha256=meta['sha256'];run.finished_at=datetime.now(timezone.utc)
        db.commit();mark_result(db,data.source_code)
        return {'run_id':run.id,'source_code':data.source_code,'status':'success','format':fmt,'records_seen':len(rows),'records_saved':len(rows),'bytes_downloaded':meta['bytes_downloaded'],'content_sha256':meta['sha256'],'source_url':meta['final_url'],'note':'Raw мөр, стандартчилсан талбар, эх URL, SHA-256 хадгаллаа.'}
    except ConnectorError as exc:
        from datetime import datetime,timezone
        db.rollback();failed=db.get(SourceSyncRun,run.id)
        if failed:failed.status='failed';failed.error_message=str(exc)[:2000];failed.finished_at=datetime.now(timezone.utc);db.commit()
        mark_result(db,data.source_code,str(exc));raise HTTPException(502,str(exc))
    except Exception as exc:
        from datetime import datetime,timezone
        db.rollback();failed=db.get(SourceSyncRun,run.id)
        if failed:failed.status='failed';failed.error_message=type(exc).__name__;failed.finished_at=datetime.now(timezone.utc);db.commit()
        mark_result(db,data.source_code,type(exc).__name__);raise HTTPException(500,'Эх сурвалж боловсруулах үед дотоод алдаа гарлаа.')

@app.get('/api/v1/admin/sync-runs',dependencies=[Depends(require_admin)])
def sync_runs(limit:int=Query(default=50,ge=1,le=200),source_code:str|None=None,db:Session=Depends(get_db)):
    stmt=select(SourceSyncRun).order_by(SourceSyncRun.id.desc()).limit(limit)
    if source_code:stmt=select(SourceSyncRun).where(SourceSyncRun.source_code==source_code).order_by(SourceSyncRun.id.desc()).limit(limit)
    return [{'id':r.id,'source_code':r.source_code,'source_url':r.source_url,'status':r.status,'records_seen':r.records_seen,'records_saved':r.records_saved,'bytes_downloaded':r.bytes_downloaded,'content_type':r.content_type,'content_sha256':r.content_sha256,'error_message':r.error_message,'started_at':r.started_at,'finished_at':r.finished_at} for r in db.scalars(stmt)]

@app.get('/api/v1/admin/source-metrics',dependencies=[Depends(require_admin)])
def source_metrics(db:Session=Depends(get_db)):
    output=[]
    for s in db.scalars(select(SourceRegistry)).all():
        count=db.scalar(select(func.count()).select_from(SourceRecord).where(SourceRecord.source_id==s.id)) or 0
        latest=db.scalar(select(SourceSyncRun).where(SourceSyncRun.source_code==s.code).order_by(SourceSyncRun.id.desc()).limit(1))
        output.append({'code':s.code,'name':s.name,'url':s.url,'records_total':count,'last_success_at':s.last_success_at,'last_error':s.last_error,'latest_run_status':latest.status if latest else None,'latest_run_at':latest.finished_at if latest else None,'latest_records_saved':latest.records_saved if latest else 0,'latest_error':latest.error_message if latest else None})
    return output
