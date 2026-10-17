"""Import previously downloaded, permitted normalized JSON files; no unapproved site scraping."""
import argparse, json
from pathlib import Path
from .db import Base, engine, SessionLocal
from .main import SOURCE_SEEDS
from .models import SourceRegistry
from .services import ingest_tender, evaluate_r01
from sqlalchemy import select

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--file',required=True,help='JSON array of validated, normalized tenders')
    parser.add_argument('--source',default='tender_official')
    args=parser.parse_args()
    path=Path(args.file)
    if path.stat().st_size>5_000_000:raise ValueError('Import file larger than 5MB')
    data=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,list):raise ValueError('Expected JSON array')
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        for code,name,url,method in SOURCE_SEEDS:
            if not db.scalar(select(SourceRegistry).where(SourceRegistry.code==code)):
                db.add(SourceRegistry(code=code,name=name,url=url,access_method=method))
        db.commit()
        count=0
        for record in data:
            _,changed=ingest_tender(db,record,args.source)
            count+=int(changed)
        risks=evaluate_r01(db)
        db.commit()
    print(f'Imported/updated {count} records; created {risks} R01 risk indicators')
if __name__=='__main__': main()
