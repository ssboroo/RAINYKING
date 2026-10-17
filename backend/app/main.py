from contextlib import asynccontextmanager
import hashlib, json
from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session
from .config import settings
from .db import Base, engine, get_db
from .models import SourceRegistry, SourceRecord, Company, Organization, Tender, TenderPlan, TenderSupplier, Evidence, RiskFinding, ReviewAction, AuditLog, Relationship
from .services import ingest_tender, evaluate_r01
from .connectors import connector_catalog, tender_invitations, tender_plans, tender_suppliers, ConnectorError
from .integrations import upsert_connector_registry, catalog_status, mark_result

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

def store_source_payload(db:Session, external_id:str, payload:dict, source_url:str='https://www.tender.gov.mn/')->SourceRecord:
    source=db.scalar(select(SourceRegistry).where(SourceRegistry.code=='tender_official'))
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
    term=f'%{q}%'
    return {'tenders':[tender_dict(x,db) for x in db.scalars(select(Tender).where(or_(Tender.title.ilike(term),Tender.external_id.ilike(term))).limit(30))], 'companies':[{'id':x.id,'name':x.name,'registration':x.registration,'resolution_status':x.resolution_status} for x in db.scalars(select(Company).where(Company.name.ilike(term)).limit(30))], 'organizations':[{'id':x.id,'name':x.name} for x in db.scalars(select(Organization).where(Organization.name.ilike(term)).limit(30))]}

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

@app.get('/api/v1/risks')
def risks(db:Session=Depends(get_db)):
    return [{'id':x.id,'rule_code':x.rule_code,'title':x.title,'entity_type':x.entity_type,'entity_id':x.entity_id,'severity':x.severity,'confidence':x.confidence,'explanation':x.explanation,'limitations':x.limitations,'evidence_id':x.evidence_id,'review_status':x.review_status} for x in db.scalars(select(RiskFinding).order_by(RiskFinding.id.desc()).limit(100))]

@app.get('/api/v1/evidence/{eid}')
def evidence(eid:int,db:Session=Depends(get_db)):
    x=db.get(Evidence,eid)
    if not x:raise HTTPException(404,'Evidence not found')
    return {'id':x.id,'source_url':x.source_url,'document_hash':x.document_hash,'excerpt':x.excerpt,'page_number':x.page_number,'verified':x.verified}

@app.post('/api/v1/admin/import/tender',dependencies=[Depends(require_admin)])
def import_tender(data:TenderIn,db:Session=Depends(get_db)):
    try: obj,changed=ingest_tender(db,data.model_dump(exclude_none=True)); db.commit()
    except ValueError as exc: db.rollback(); raise HTTPException(422,str(exc))
    return {'id':obj.id,'changed':changed}

@app.post('/api/v1/admin/evaluate',dependencies=[Depends(require_admin)])
def evaluate(db:Session=Depends(get_db)):
    count=evaluate_r01(db)
    db.commit()
    return {'new_findings':count,'implemented_rules':['R01'],'status':'review_required'}

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

@app.post('/api/v1/admin/sync/tenders',dependencies=[Depends(require_admin)])
async def sync_tenders(data:SyncIn,db:Session=Depends(get_db)):
    imported=0; pages=0; total=0
    try:
        for page in range(1,data.max_pages+1):
            body=await tender_invitations(page,data.per_page,data.year);pages+=1
            records=body.get('data',[]);total=int(body.get('total') or 0)
            for item in records:
                code=str(item.get('tenderCode') or item.get('invitationNumber') or item.get('invitationId') or '').strip()
                title=str(item.get('tenderName') or '').strip()
                if not code or not title:continue
                payload={'external_id':code,'title':title,'source_url':'https://www.tender.gov.mn/','organization_name':item.get('budgetEntityName') or item.get('budgetGovernerName'),'organization_official_id':item.get('registrationNumber') or item.get('budgetEntityCode'),'budget_amount':safe_float(item.get('totalBudget')),'procurement_method':item.get('ruleName') or item.get('tenderTypeName'),'published_at':item.get('publishDate') or item.get('publishedDate')}
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
                rec=store_source_payload(db,'plan:'+ext,item)
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
                external=f'{invitation}:{reg or name.casefold()}'
                rec=store_source_payload(db,'supplier:'+external,item)
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
