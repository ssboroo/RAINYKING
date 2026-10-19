"""Persist only structured profiles returned by the fixed public-source adapter."""
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit
from sqlalchemy import select
from .models import SourceRegistry, SourceRecord
from .normalization import normalize_record


def cache_profiles(db, records):
    source=db.scalar(select(SourceRegistry).where(SourceRegistry.code=='shilendans'))
    if source is None:
        return 0
    count=0
    for item in records:
        url=urlsplit(item.get('url',''))
        row=item.get('row',{})
        if (url.scheme!='https' or url.hostname!='shilendans.gov.mn' or not url.path.startswith('/organization/')
                or item.get('format')!='organization_profile' or not row.get('organization_name')):
            continue
        payload={'kind':'public_source_record','format':'organization_profile','raw':row,
                 'normalized':normalize_record(row),'content_sha256':item['content_sha256'],
                 'extraction_method':'browser_form'}
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        identity='shilendans-profile:'+url.path.rsplit('/',1)[-1]
        exists=db.scalar(select(SourceRecord).where(SourceRecord.source_id==source.id,SourceRecord.external_id==identity,SourceRecord.sha256==digest))
        if exists is None:
            db.add(SourceRecord(source_id=source.id,external_id=identity,raw_payload=payload,sha256=digest,source_url=item['url']))
        count+=1
    if count:
        source.last_success_at=datetime.now(timezone.utc)
        source.last_error=None
        db.commit()
    return count
