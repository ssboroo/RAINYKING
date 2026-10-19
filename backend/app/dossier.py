"""Subject dossiers: exact-ID joins, source claims, discrepancies and coverage gaps."""
from datetime import datetime,timezone
from sqlalchemy import select
from .record_search import record_fields,matches_record,record_title,key as field_key
from .models import SearchObservation
from .search_observations import query_hash
from .connectors import connector_catalog


def registration_key(value):
    return ''.join(str(value).split()).upper()


def select_subject_records(latest,q,registration):
    initial=[]; registrations=set()
    for record,source in latest.values():
        found=matches_record(record.raw_payload,q=q,registration=registration)
        if found is not None:
            initial.append((record,source,'query_match'))
            values={registration_key(v) for v in found['fields']['registration']}
            if len(values)==1:registrations.update(values)
    chosen={r.id:(r,s,basis) for r,s,basis in initial}
    if initial or (registration and not q):
        if registration: registrations.add(registration_key(registration))
        for record,source in latest.values():
            if record.id in chosen or matches_record(record.raw_payload) is None: continue
            fields,_=record_fields(record.raw_payload)
            if registrations.intersection(registration_key(v) for v in fields['registration']):
                chosen[record.id]=(record,source,'exact_registration')
    return list(chosen.values())


FACTS={
 'organization_type':('Байгууллагын төрөл',('organization_type','entity_type','Байгууллагын төрөл')),
 'parent_organization_name':('Толгой байгууллага',('parent_organization_name','parent_organization','Толгой байгууллага')),
 'state_share_percent':('Төрийн хувь оролцоо',('state_share_percent','Төрд ногдох хувь хөрөнгө')),
 'organization_status':('Бүртгэлийн төлөв',('organization_status','registration_status','Бүртгэлийн төлөв')),
 'registered_at':('Бүртгэсэн огноо',('registered_at','registration_date','Анх үүсгэн байгуулагдсан огноо')),
}
CHECKS=[
 ('identity','Байгууллага ба регистр',('company_registry','shilendans')),
 ('status','Хүчинтэй бүртгэл / татан буулгалт',('company_registry',)),
 ('ownership','Эзэмшигч ба эцсийн өмчлөгч',('company_registry',)),
 ('parent','Толгой байгууллага / хувь оролцоо',('company_registry','shilendans')),
 ('procurement','Тендер ба гэрээ',('tender_official',)),
 ('transactions','Мөнгөн гүйлгээ',('shilendans',)),
 ('audit','Аудитын дүгнэлт',('audit',)),
 ('court','Шүүхийн шийдвэр',('courts',)),
 ('licenses','Лиценз ба тусгай зөвшөөрөл',()),
 ('land','Газар ба байгалийн баялаг',()),
 ('funds','Тусгай сан, татаас, зээл',()),
 ('officials','ХОМ ба ашиг сонирхол',('iaac',)),
 ('political_finance','Улс төрийн санхүүжилт',()),
]


def aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def build_dossier(db,selected,q,registration,tenders):
    now=datetime.now(timezone.utc)
    groups={}; documents=[]; typed_evidence={}
    for record,source,basis in selected:
        fields,_=record_fields(record.raw_payload)
        keys=set(registration_key(v) for v in fields['registration'])
        if len(keys)>1:
            documents.append({'record_id':record.id,'title':record_title(record.raw_payload,source.name),'source_url':record.source_url,'reason':'Олон регистртэй нэг баримт; талбарын эзэн тодорхойгүй тул профайлд автоматаар холбоогүй.'})
            continue
        if not keys:
            documents.append({'record_id':record.id,'title':record_title(record.raw_payload,source.name), 'source_url':record.source_url,'reason':'Этгээдийн регистр танигдаагүй; автоматаар холбоогүй.'})
        raw=record.raw_payload.get('raw',record.raw_payload) if isinstance(record.raw_payload,dict) else {}
        raw=raw if isinstance(raw,dict) else {}
        # Structured identifiers qualify a category; a homepage mention or
        # report catalogue does not count as a transaction/audit/court record.
        markers={
            'transactions':{'transaction id','payment id','гүйлгээний дугаар'},
            'audit':{'audit report id','audit opinion','аудитын дүгнэлт'},
            'court':{'case number','case id','decision id','хэргийн дугаар'},
            'licenses':{'license number','лицензийн дугаар'},
            'land':{'parcel id','нэгж талбарын дугаар'},
            'funds':{'subsidy id','loan id','fund report id'},
            'officials':{'declaration id','income declaration id'},
            'political_finance':{'campaign finance report id','political donation id'},
        }
        present={field_key(k) for k,v in raw.items() if isinstance(v,(str,int,float)) and str(v).strip()}
        if len(keys)==1:
            for category,aliases in markers.items():
                if present.intersection(aliases):typed_evidence.setdefault(category,[]).append(record.id)
        for reg in sorted(keys):
            subject=groups.setdefault(reg,{'registration':reg,'names':[],'record_ids':[],'source_codes':[],'facts':{},'relationships':[],'conflicts':[],'stale_records':[]})
            for name in fields['company_name']+fields['organization_name']:
                if name not in subject['names']: subject['names'].append(name)
            subject['record_ids'].append(record.id)
            if source.code not in subject['source_codes']:subject['source_codes'].append(source.code)
            proof={'record_id':record.id,'source_code':source.code,'source_name':source.name,'source_url':record.source_url,'fetched_at':record.fetched_at,'sha256':record.sha256,'match_basis':basis}
            if (now-aware(record.fetched_at)).days>30: subject['stale_records'].append(record.id)
            for field,(label,aliases) in FACTS.items():
                value=next((raw[a] for a in aliases if a in raw and isinstance(raw[a],(str,int,float)) and str(raw[a]).strip()),None)
                if value is None:continue
                subject['facts'].setdefault(field,{'label':label,'claims':[]})['claims'].append({'value':str(value),'verification':'source_claim',**proof})
                if field=='parent_organization_name':
                    subject['relationships'].append({'kind':'declared_parent','label':'Толгой байгууллага гэж мэдээлсэн','target_name':str(value),'target_registration':None,'verification':'source_claim',**proof})
            for owner in fields['owner_name']:
                subject['relationships'].append({'kind':'owner_mention','label':'Эзэмшигчийн эх сурвалжийн мэдэгдэл','target_name':owner,'target_registration':None,'verification':'source_claim',**proof})
    if registration and not groups:
        reg=registration_key(registration)
        groups[reg]={'registration':reg,'names':[],'record_ids':[],'source_codes':[],'facts':{},'relationships':[],'conflicts':[],'stale_records':[]}
    for subject in groups.values():
        for key,fact in subject['facts'].items():
            values=list(dict.fromkeys(c['value'] for c in fact['claims']))
            if len(values)>1:
                subject['conflicts'].append({'field':key,'label':fact['label'],'values':values,'status':'needs_review','note':'Эх сурвалжийн утгууд зөрүүтэй. Огноо ба эх баримтыг тулгана; автоматаар нэгийг сонгохгүй.'})
        subject['identity_status']='single_source_id' if len(subject['source_codes'])==1 else ('cross_source_id' if len(subject['source_codes'])>1 else 'not_found')
        subject['legal_status_verified']=False
    fingerprints={query_hash(value) for value in (q,registration) if value.strip()}
    observations={}
    if fingerprints:
        for obs in db.scalars(select(SearchObservation).where(SearchObservation.query_hash.in_(fingerprints)).order_by(SearchObservation.observed_at.desc())):
            if obs.source_code not in observations:observations[obs.source_code]=obs
    source_coverage=[]
    for source in connector_catalog():
        obs=observations.get(source['code'])
        linked=sum(source['code'] in subject['source_codes'] for subject in groups.values())
        page_states={p.get('status') for p in obs.pages} if obs else set()
        if obs is None:status='not_searched'
        elif obs.status=='busy':status='busy'
        elif 'robots_denied' in page_states or obs.status=='robots_denied':status='access_restricted'
        elif obs.status in ('failed','timeout','robots_unavailable') or (obs.pages and not any(p.get('status')=='read' for p in obs.pages)):status='unavailable'
        elif linked:status='id_evidence_found'
        elif obs.matches:status='text_matches_only'
        else:status='no_match_in_scope'
        source_coverage.append({'code':source['code'],'name':source['name'],'url':source['public_web_url'],'status':status,
            'last_query_check_at':obs.observed_at if obs else None,'pages_checked':len(obs.pages) if obs else 0,
            'raw_status':obs.status if obs else None,'note':obs.note if obs else '',
            'pages':obs.pages if obs else [],'id_linked_subjects':linked,
            'acquisition':'organization_browser_adapter' if source['code']=='shilendans' else 'bounded_public_crawl'})
    checks=[]
    subjects=list(groups.values())
    for code,label,sources in CHECKS:
        supported=bool(sources)
        if code=='identity': found=any(s['record_ids'] for s in subjects)
        elif code=='status': found=any(s['facts'].get('organization_status') for s in subjects)
        elif code=='parent':found=any(any(r['kind']=='declared_parent' for r in s['relationships']) or s['facts'].get('state_share_percent') for s in subjects)
        elif code=='ownership':found=any(any(r['kind']=='owner_mention' for r in s['relationships']) for s in subjects)
        elif code=='procurement':found=bool(tenders)
        else:found=bool(typed_evidence.get(code))
        checks.append({'code':code,'label':label,'source_codes':list(sources),
            'status':'source_evidence' if found else ('no_structured_evidence' if supported else 'adapter_missing'),
            'evidence_record_ids':typed_evidence.get(code,[]),
            'note':'Хамрагдсан эх баримтын мэдээлэл; хүчинтэй байдал, бүрэн хамрах хүрээг батлаагүй.' if found else 'Шалгах бүтэцтэй баримт хамрагдаагүй. Зөрчилгүй гэсэн дүгнэлт биш.'})
    return {'subjects':subjects,'unresolved_documents':documents,'checks':checks,'sources':source_coverage,
        'counts':{'subjects':len(subjects),'exact_id_records':len({rid for s in subjects for rid in s['record_ids']}),
                  'source_claim_relationships':sum(len(s['relationships']) for s in subjects),
                  'conflicts':sum(len(s['conflicts']) for s in subjects),'query_sources_attempted':sum(s['last_query_check_at'] is not None and s['status']!='busy' for s in source_coverage),'catalog_sources':len(source_coverage)},
        'note':'Регистрээр таарсан баримтыг нэг этгээдийн лавлагаанд харуулна. Нэрээр таарсан этгээдүүд тусдаа. Эх сурвалжийн мэдэгдэл, хандалтын төлөв нь хууль ёсны хүчинтэй төлөв эсвэл гэм бурууг тогтоохгүй.'}
