from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import SourceRegistry
from .connectors import connector_catalog
def upsert_connector_registry(db:Session):
    for item in connector_catalog():
        source=db.scalar(select(SourceRegistry).where(SourceRegistry.code==item["code"]))
        if source is None:
            source=SourceRegistry(code=item["code"],name=item["name"],url=item["base_url"],access_method=item["kind"],license_notes=item["note"]);db.add(source)
        else:
            source.name=item["name"];source.url=item["base_url"];source.access_method=item["kind"];source.license_notes=item["note"]
    db.commit()
def catalog_status(db:Session):
    rows={x.code:x for x in db.scalars(select(SourceRegistry)).all()};out=[]
    for item in connector_catalog():
        row=rows.get(item["code"])
        out.append({**item,"last_success_at":row.last_success_at if row else None,"last_error":row.last_error if row else None,"status":"connected" if row and row.last_success_at else ("ready_for_token" if item["automated"] else "manual_or_schema_required")})
    return out
def mark_result(db:Session,code:str,error:str|None=None):
    row=db.scalar(select(SourceRegistry).where(SourceRegistry.code==code))
    if row:
        if error:row.last_error=error[:1000]
        else:row.last_success_at=datetime.now(timezone.utc);row.last_error=None
        db.commit()
