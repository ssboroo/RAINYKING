from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={'check_same_thread': False} if settings.database_url.startswith('sqlite') else {})
def configure_sqlite(connection, _record):
    connection.create_function('lower', 1, lambda value: value.casefold() if value is not None else None, deterministic=True)

if settings.database_url.startswith('sqlite'):
    event.listen(engine, 'connect', configure_sqlite)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
class Base(DeclarativeBase):
    pass

def get_db():
    with SessionLocal() as session:
        yield session
