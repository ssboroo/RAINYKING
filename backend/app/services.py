import hashlib, json
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import SourceRegistry, SourceRecord, Company, Organization, Tender, Evidence, RiskFinding

def canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()

def ingest_tender(session: Session, payload: dict, source_code='tender_official') -> tuple[Tender, bool]:
    """Strict normalized import: caller maps actual, verified upstream fields to this contract."""
    ext = str(payload['external_id']).strip()
    title = str(payload['title']).strip()
    url = str(payload['source_url']).strip()
    if not ext or not title or not url.startswith('https://'):
        raise ValueError('external_id, title, valid HTTPS source_url required')
    source = session.scalar(select(SourceRegistry).where(SourceRegistry.code == source_code))
    if source is None:
        raise ValueError('source not registered')
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    stored = session.scalar(select(SourceRecord).where(SourceRecord.source_id == source.id, SourceRecord.external_id == ext, SourceRecord.sha256 == digest))
    if stored:
        tender = session.scalar(select(Tender).where(Tender.external_id == ext))
        if tender: return tender, False
    stored = SourceRecord(source_id=source.id, external_id=ext, raw_payload=payload, sha256=digest, source_url=url)
    session.add(stored)
    session.flush()
    org_id = None
    if payload.get('organization_name'):
        official_id = payload.get('organization_official_id')
        org = session.scalar(select(Organization).where(Organization.official_id == str(official_id))) if official_id else None
        if not org:
            # A matching name is NOT definitive identity. Make a distinct local organization until verified.
            org = Organization(official_id=str(official_id) if official_id else None, name=str(payload['organization_name']))
            session.add(org)
            session.flush()
        org_id = org.id
    company_id = None
    if payload.get('company_name'):
        reg = payload.get('company_registration')
        company = session.scalar(select(Company).where(Company.registration == str(reg))) if reg else None
        if not company:
            company = Company(name=str(payload['company_name']), registration=str(reg) if reg else None, resolution_status='verified_identifier' if reg else 'pending_review', source_record_id=stored.id)
            session.add(company)
            session.flush()
        company_id = company.id
    tender = session.scalar(select(Tender).where(Tender.external_id == ext))
    values = {'title': title, 'organization_id': org_id, 'awardee_company_id': company_id, 'budget_amount': payload.get('budget_amount'), 'bid_count': payload.get('bid_count'), 'procurement_method': payload.get('procurement_method'), 'published_at': payload.get('published_at'), 'source_record_id': stored.id}
    if tender:
        for k, v in values.items(): setattr(tender, k, v)
    else:
        tender = Tender(external_id=ext, **values)
        session.add(tender)
    source.last_success_at = datetime.now(timezone.utc)
    source.last_error = None
    session.flush()
    return tender, True

def evaluate_r01(session: Session) -> int:
    """Only one-bid tenders with non-null evidence; confidence is limited by available fields."""
    count = 0
    for tender in session.scalars(select(Tender).where(Tender.bid_count == 1)).all():
        rec = session.get(SourceRecord, tender.source_record_id)
        if not rec: continue
        evidence = session.scalar(select(Evidence).where(Evidence.source_record_id == rec.id, Evidence.excerpt == 'bid_count=1'))
        if not evidence:
            evidence = Evidence(source_record_id=rec.id, source_url=rec.source_url, document_hash=rec.sha256, excerpt='bid_count=1')
            session.add(evidence)
            session.flush()
        finding = session.scalar(select(RiskFinding).where(RiskFinding.rule_code == 'R01', RiskFinding.entity_type == 'tender', RiskFinding.entity_id == tender.id))
        if finding: continue
        session.add(RiskFinding(rule_code='R01', title='Өрсөлдөөний сул оролцооны дохио', entity_type='tender', entity_id=tender.id, severity='medium', confidence=0.65, explanation='Эх сурвалжид бүртгэгдсэн саналын тоо 1 байна. Энэ нь дангаараа зөрчил нотлохгүй.', limitations='Худалдан авах ажиллагааны төрөл, зарын хугацаа, эх өгөгдлийн бүрэн байдлыг шинжээч нягтална.', evidence_id=evidence.id))
        count += 1
    session.flush()
    return count
