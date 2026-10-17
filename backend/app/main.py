from contextlib import asynccontextmanager
import hashlib, json, re
from difflib import SequenceMatcher
from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session
from .config import settings
from .db import Base, engine, get_db
from .models import SourceRegistry, SourceRecord, Company, Organization, Tender, TenderPlan, TenderSupplier, Evidence, RiskFinding, ReviewAction, AuditLog, Relationship, SourceSyncRun
from .services import ingest_tender, evaluate_r01
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
                payload={'external_id':code,'title':title,'source_url':('https://www.tender.gov.mn/mn/embed/'+str(item.get('invitationId'))) if item.get('invitationId') else 'https://www.tender.gov.mn/','organization_name':item.get('budgetEntityName') or item.get('budgetGovernerName'),'organization_official_id':item.get('registrationNumber') or item.get('budgetEntityCode'),'budget_amount':safe_float(item.get('totalBudget')),'procurement_method':item.get('ruleName') or item.get('tenderTypeName'),'published_at':item.get('publishDate') or item.get('publishedDate')}
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

def stable_record_identity(normalized, row):
    strong = normalized.get("registration") or normalized.get("tender_id") or normalized.get("contract_id")
    if strong:
        return hashlib.sha256(str(strong).casefold().strip().encode("utf-8")).hexdigest()[:32]
    # Fallback to canonical row content; avoids row-position based identity.
    canonical=json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]

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
