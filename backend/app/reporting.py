"""Reproducible evidence summaries. No model-generated accusations or inferred identities."""
from datetime import datetime, timezone
from sqlalchemy import select
from .models import SourceRecord, SourceRegistry, Tender, Company, Organization, RiskFinding, Evidence
from .record_search import matches_record


def build_report(db, q='', registration=''):
    latest = {}
    versions = 0
    # Latest imported version per source record identity; old snapshots are not extra events.
    for record, source in db.execute(select(SourceRecord, SourceRegistry).join(SourceRegistry, SourceRegistry.id == SourceRecord.source_id).order_by(SourceRecord.fetched_at.desc(), SourceRecord.id.desc()).execution_options(yield_per=200)):
        identity = (record.source_id, record.external_id)
        if identity in latest:
            versions += 1
            continue
        payload = record.raw_payload if isinstance(record.raw_payload, dict) else {}
        raw = payload.get('raw', {})
        if payload.get('kind') == 'public_web_document' and payload.get('format') == 'html_text' and isinstance(raw, dict) and len(str(raw.get('text', ''))) < 150:
            continue
        latest[identity] = (record, source)
    matches = []
    counts = {}
    registrations = set()
    owners = []
    record_ids = set()
    for record, source in latest.values():
        found = matches_record(record.raw_payload, q=q, registration=registration)
        if found is None:
            continue
        record_ids.add(record.id)
        counts[source.code] = counts.get(source.code, 0) + 1
        for value in found['fields']['registration']:
            registrations.add(''.join(value.split()).upper())
        for name in found['fields']['owner_name']:
            owners.append({'name': name, 'source_record_id': record.id, 'source_url': record.source_url, 'status': 'source_claim'})
        matches.append({'id': record.id, 'external_id': record.external_id, 'source_code': source.code,
                        'source_name': source.name, 'source_url': record.source_url, 'sha256': record.sha256,
                        'fetched_at': record.fetched_at, 'fields': found['fields'], 'matched_fields': found['matched_fields']})
    # SQL types are evaluated through exact foreign keys, not name similarity.
    companies = [c for c in db.scalars(select(Company)) if c.registration and ''.join(c.registration.split()).upper() in registrations]
    company_ids = {c.id for c in companies}
    tenders = [t for t in db.scalars(select(Tender)) if t.source_record_id in record_ids or t.awardee_company_id in company_ids]
    tender_ids = {t.id for t in tenders}
    organizations = {o.id: o for o in db.scalars(select(Organization))}
    tender_rows = []
    for tender in tenders:
        record = db.get(SourceRecord, tender.source_record_id)
        org = organizations.get(tender.organization_id)
        tender_rows.append({'id': tender.id, 'external_id': tender.external_id, 'title': tender.title,
                            'organization_name': org.name if org else None, 'budget_amount': tender.budget_amount,
                            'currency': tender.currency, 'bid_count': tender.bid_count,
                            'source_url': record.source_url if record else None,
                            'sha256': record.sha256 if record else None})
    signals = []
    for risk in db.scalars(select(RiskFinding).order_by(RiskFinding.id)):
        if not ((risk.entity_type == 'tender' and risk.entity_id in tender_ids) or (risk.entity_type == 'company' and risk.entity_id in company_ids)):
            continue
        evidence = db.get(Evidence, risk.evidence_id)
        # Keep rejected findings in separate history; they must never support a conclusion.
        signals.append({'id': risk.id, 'rule_code': risk.rule_code, 'title': risk.title,
                        'explanation': risk.explanation, 'limitations': risk.limitations,
                        'review_status': risk.review_status, 'evidence_id': risk.evidence_id,
                        'source_url': evidence.source_url if evidence else None,
                        'sha256': evidence.document_hash if evidence else None})
    known_bids = [t for t in tenders if t.bid_count is not None]
    single = sum(t.bid_count == 1 for t in known_bids)
    budgets = {}
    for tender in tenders:
        if tender.budget_amount is not None:
            budgets[tender.currency] = budgets.get(tender.currency, 0) + tender.budget_amount
    coverage = []
    for source in db.scalars(select(SourceRegistry).order_by(SourceRegistry.code)):
        count = sum(1 for _, s in latest.values() if s.id == source.id)
        coverage.append({'code': source.code, 'name': source.name, 'url': source.url,
                         'imported_records': count, 'matched_records': counts.get(source.code, 0),
                         'last_success_at': source.last_success_at,
                         'status': 'has_imported_data' if count else 'no_imported_data', 'crawl_note': source.access_notes or None})
    active_signals = [s for s in signals if s['review_status'] not in ('rejected', 'dismissed')]
    conclusions = [f'{len(matches)} эх баримт {len(counts)} эх сурвалжаас хайлтын нөхцөлд таарсан.',
                   f'Албан регистр эсвэл эх баримтын ID холбоосоор {len(tenders)} тендер хамрагдсан.',
                   f'Саналын тоо тодорхой {len(known_bids)} тендерээс {single} нь нэг саналтай.']
    if not owners:
        conclusions.append('Хамрагдсан баримтад танигдсан эзэмшигчийн мэдээлэл алга; эзэмшлийг тогтоох боломжгүй.')
    if not matches:
        conclusions.append('Хайлтад таарсан импортын баримт алга. Энэ нь холбоо, зөрчил байхгүй гэсэн дүгнэлт биш.')
    return {'generated_at': datetime.now(timezone.utc), 'query': {'q': q, 'registration': registration},
            'summary': {'records': len(matches), 'sources': len(counts), 'linked_tenders': len(tenders),
                        'known_bid_count': len(known_bids), 'single_bid_count': single,
                        'budget_by_currency': budgets, 'review_signals': len(active_signals),
                        'excluded_older_versions': versions},
            'conclusions': conclusions, 'records': matches, 'tenders': tender_rows,
            'owner_mentions': owners, 'signals': active_signals,
            'dismissed_signals': [s for s in signals if s not in active_signals], 'source_coverage': coverage,
            'methodology': ['Импортолсон эх баримтын эх сурвалж + external_id тус бүрийн сүүлийн хувилбарыг сонгоно.',
                            'Хайлтын нэр нь зөвхөн баримт олох нөхцөл; ижил хүнийг тогтоох шалгуур биш.',
                            'Регистр, эх бүртгэлийн ID болон бүртгэгдсэн байгууллага–тендерийн холбоосыг ашиглана.',
                            'Төсөв бол бодит төлбөр биш. Валют бүрийг тусад нь нэгтгэнэ.',
                            'Эрсдэлийн дохио нь авлига, албан тушаал урвуулсан үйлдлийг тогтоосон шийдвэр биш.'],
            'limitations': ['Зөвхөн импортолсон эх сурвалжийн хүрээнд; бүх улсын сангийн бүрэн шалгалт биш.',
                           'Танигдсан эзэмшигчийн талбар нь эх сурвалжийн мэдэгдэл; хүчинтэй өмчлөл ба хүний хэн болохыг тусад нь шалгана.',
                           'ХОМ, лиценз, газар, санхүүжилт болон аудитын холбоосыг баталгаатай ID/schema импортлоогүй бол дүгнэхгүй.']}
