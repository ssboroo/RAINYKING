import hashlib, json, re
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import SourceRegistry, SourceRecord, Company, Organization, Tender, TenderPlan, TenderSupplier, Evidence, RiskFinding

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


def evaluate_risk_signals(session: Session) -> dict:
    """Generate explainable, source-backed review signals; never label a subject corrupt."""
    created = {"R01": evaluate_r01(session), "R02": 0, "R03": 0}
    # R02: same registered supplier appears in at least three distinct invitations,
    # and at least one linked tender has only one recorded bid.
    suppliers = session.scalars(select(TenderSupplier).where(TenderSupplier.supplier_register.is_not(None))).all()
    tender_by_invitation = {}
    for tender in session.scalars(select(Tender)).all():
        rec = session.get(SourceRecord, tender.source_record_id)
        raw = rec.raw_payload if rec and isinstance(rec.raw_payload, dict) else {}
        invitation = str(raw.get("upstream_invitation_id") or "").strip()
        if invitation:
            tender_by_invitation.setdefault(invitation, []).append(tender)
    by_registration = {}
    for supplier in suppliers:
        reg = re.sub(r"\s+", "", str(supplier.supplier_register or "")).upper()
        if reg:
            by_registration.setdefault(reg, []).append(supplier)
    for reg, rows in by_registration.items():
        invitations = sorted({str(row.invitation_id) for row in rows})
        linked = [t for inv in invitations for t in tender_by_invitation.get(inv, [])]
        single_bid = [t for t in linked if t.bid_count == 1]
        if len(invitations) < 3 or not single_bid:
            continue
        for row in rows:
            existing = session.scalar(select(RiskFinding).where(RiskFinding.rule_code == "R02", RiskFinding.entity_type == "supplier", RiskFinding.entity_id == row.id))
            if existing:
                continue
            rec = session.get(SourceRecord, row.source_record_id)
            if not rec:
                continue
            evidence = session.scalar(select(Evidence).where(Evidence.source_record_id == rec.id, Evidence.excerpt.like(f"%supplier_register={reg}%")))
            if not evidence:
                excerpt = f"supplier_register={reg}; distinct_invitations={len(invitations)}; one_bid_tenders={len(single_bid)}; invitation_ids={','.join(invitations[:30])}"
                evidence = Evidence(source_record_id=rec.id, source_url=rec.source_url, document_hash=rec.sha256, excerpt=excerpt, verified=False)
                session.add(evidence)
                session.flush()
            session.add(RiskFinding(rule_code="R02", title="Нийлүүлэгчийн давтамж ба бага өрсөлдөөний дохио", entity_type="supplier", entity_id=row.id, severity="medium", confidence=0.55, explanation=f"Ижил регистртэй нийлүүлэгч {len(invitations)} өөр урилгад бүртгэгдсэн бөгөөд тэдгээрийн холбогдсон мэдээлэлд нэг саналтай {len(single_bid)} тендер байна. Энэ нь давтан оролцоо + бага өрсөлдөөнийг хамтад нь нягтлах дохио юм.", limitations="Нийлүүлэгч олон тендерт оролцох нь хэвийн байж болно. Invitation ID-ийн холбоос болон саналын тооны бүрэн байдлыг эх сурвалжаас шалгах шаардлагатай; авлигын нотолгоо биш.", evidence_id=evidence.id))
            created["R02"] += 1
    # R03: exact tender-code match between plan and tender, with >20% budget difference.
    plans = session.scalars(select(TenderPlan).where(TenderPlan.tender_code.is_not(None), TenderPlan.budget_amount.is_not(None))).all()
    for plan in plans:
        tender = session.scalar(select(Tender).where(Tender.external_id == plan.tender_code))
        if not tender or tender.budget_amount is None or float(plan.budget_amount or 0) <= 0:
            continue
        delta = abs(float(tender.budget_amount) - float(plan.budget_amount)) / float(plan.budget_amount)
        if delta <= 0.20:
            continue
        existing = session.scalar(select(RiskFinding).where(RiskFinding.rule_code == "R03", RiskFinding.entity_type == "tender", RiskFinding.entity_id == tender.id))
        if existing:
            continue
        rec = session.get(SourceRecord, tender.source_record_id)
        plan_rec = session.get(SourceRecord, plan.source_record_id)
        if not rec:
            continue
        evidence = Evidence(source_record_id=rec.id, source_url=rec.source_url, document_hash=rec.sha256, excerpt=f"tender_code={plan.tender_code}; plan_budget={plan.budget_amount}; tender_budget={tender.budget_amount}; variance_pct={round(delta*100,2)}; plan_source_url={plan_rec.source_url if plan_rec else 'unknown'}; plan_source_sha256={plan_rec.sha256 if plan_rec else 'unknown'}", verified=False)
        session.add(evidence)
        session.flush()
        session.add(RiskFinding(rule_code="R03", title="Төлөвлөгөө ба тендерийн төсвийн зөрүү", entity_type="tender", entity_id=tender.id, severity="medium", confidence=0.7, explanation=f"Яг таарсан тендерийн кодын төлөвлөгөөний дүн ба тендерийн дүн {round(delta*100, 1)}%-ийн зөрүүтэй байна.", limitations="Төсвийн хувилбар, татвар, багцын хамрах хүрээ, шинэчилсэн төлөвлөгөө болон валютын нөхцөлийг шалгана. Зөрүү нь дангаараа хууль бус үйлдлийг нотлохгүй.", evidence_id=evidence.id))
        created["R03"] += 1
    session.flush()
    return created
