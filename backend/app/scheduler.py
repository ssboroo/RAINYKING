"""Optional periodic public-source sync worker.
Configure AUTO_SYNC_SOURCES as a JSON list of {"source_code": "...", "url": "https://..."}.
"""
import asyncio, hashlib, json, os
from datetime import datetime, timezone
from sqlalchemy import select
from .db import Base, engine, SessionLocal
from .models import SourceRegistry, SourceRecord, SourceSyncRun
from .connectors import fetch_registered_public_resource, validate_public_url, ConnectorError
from .normalization import normalize_record
from .integrations import upsert_connector_registry, mark_result

def source_configs():
    raw=os.getenv("AUTO_SYNC_SOURCES","").strip()
    if not raw:
        return []
    items=json.loads(raw)
    if not isinstance(items,list):
        raise ValueError("AUTO_SYNC_SOURCES must be a JSON array")
    return items

def store_row(db, source, external_id, payload, source_url):
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")
    digest=hashlib.sha256(encoded).hexdigest()
    record=db.scalar(select(SourceRecord).where(SourceRecord.source_id==source.id,SourceRecord.external_id==external_id,SourceRecord.sha256==digest))
    if record is None:
        record=SourceRecord(source_id=source.id,external_id=external_id,raw_payload=payload,sha256=digest,source_url=source_url)
        db.add(record)
    source.last_success_at=datetime.now(timezone.utc)
    source.last_error=None

async def sync_one(config):
    code=str(config.get("source_code","")).strip()
    url=str(config.get("url","")).strip()
    if not code or not url:
        print("Skipping invalid AUTO_SYNC_SOURCES entry")
        return
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        upsert_connector_registry(db)
        source=db.scalar(select(SourceRegistry).where(SourceRegistry.code==code))
        if source is None:
            print(f"Skipping unregistered source code: {code}")
            return
        try:
            host=validate_public_url(url)
            registered=validate_public_url(source.url)
            if host!=registered and not host.endswith("."+registered):
                raise ConnectorError("Configured URL does not match registered source domain")
        except ConnectorError as exc:
            source.last_error=str(exc)[:1000]
            db.commit()
            print(f"{code}: configuration error: {exc}")
            return
        run=SourceSyncRun(source_code=code,source_url=url,status="running")
        db.add(run);db.commit();db.refresh(run)
        try:
            fmt,rows,meta=await fetch_registered_public_resource(url,max_bytes=5_000_000,max_rows=500)
            for index,row in enumerate(rows,1):
                normalized=normalize_record(row)
                identity=normalized.get("registration") or normalized.get("tender_id") or normalized.get("contract_id") or normalized.get("title") or ""
                stable=hashlib.sha256((str(identity).casefold()+"|"+str(index)).encode()).hexdigest()[:32]
                payload={"kind":"public_source_record","format":fmt,"row_number":index,"normalized":normalized,"raw":row,"content_sha256":meta["sha256"]}
                store_row(db,source,"scheduled:"+stable,payload,meta["final_url"])
            run.status="success";run.records_seen=len(rows);run.records_saved=len(rows)
            run.bytes_downloaded=meta["bytes_downloaded"];run.content_type=meta["content_type"]
            run.content_sha256=meta["sha256"];run.finished_at=datetime.now(timezone.utc)
            db.commit()
            print(f"{code}: success, saved {len(rows)} records")
        except Exception as exc:
            db.rollback()
            failed=db.get(SourceSyncRun,run.id)
            if failed:
                failed.status="failed";failed.error_message=(str(exc) if isinstance(exc,ConnectorError) else type(exc).__name__)[:2000]
                failed.finished_at=datetime.now(timezone.utc);db.commit()
            mark_result(db,code,str(exc))
            print(f"{code}: sync failed: {type(exc).__name__}")

async def main():
    Base.metadata.create_all(engine)
    interval=max(5,int(os.getenv("AUTO_SYNC_INTERVAL_MINUTES","360")))*60
    while True:
        try:
            configs=source_configs()
            if not configs:
                print("AUTO_SYNC_SOURCES is empty; scheduler idle.")
            for config in configs:
                await sync_one(config)
        except Exception as exc:
            print(f"scheduler cycle failed: {type(exc).__name__}")
        await asyncio.sleep(interval)

if __name__=="__main__":
    asyncio.run(main())
