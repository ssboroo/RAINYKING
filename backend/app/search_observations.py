import hashlib
from datetime import datetime,timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .models import SearchObservation


def query_hash(query):
    return hashlib.sha256(' '.join(query.split()).casefold().encode()).hexdigest()


def record_observation(db, query, result):
    fingerprint=query_hash(query)
    code=result['source_code']
    row=db.scalar(select(SearchObservation).where(SearchObservation.source_code==code,SearchObservation.query_hash==fingerprint))
    if row is None:
        try:
            with db.begin_nested():
                row=SearchObservation(source_code=code,query_hash=fingerprint,status='pending')
                db.add(row);db.flush()
        except IntegrityError:
            row=db.scalar(select(SearchObservation).where(SearchObservation.source_code==code,SearchObservation.query_hash==fingerprint))
    row.status=result['status']
    row.matches=len(result.get('records',[]))
    row.records_saved=result.get('records_saved',0)
    row.pages=[{k:p[k] for k in ('url','status','extraction_method','error') if k in p} for p in result.get('pages',[])][:20]
    row.note=result.get('note','')[:1000]
    # Cache hits retain the acquisition time, not a fabricated new fetch date.
    if not result.get('cached') or row.observed_at is None:
        row.observed_at=datetime.now(timezone.utc)
    db.commit()
