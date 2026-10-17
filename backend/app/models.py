from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, DateTime, Boolean, Text, ForeignKey, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

def now(): return datetime.now(timezone.utc)

class SourceRegistry(Base):
    __tablename__='source_registry'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(90), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    access_method: Mapped[str] = mapped_column(String(40), default='manual')
    access_notes: Mapped[str] = mapped_column(Text, default='')
    license_notes: Mapped[str] = mapped_column(Text, default='verification required')
    last_success_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str|None] = mapped_column(Text, nullable=True)

class SourceRecord(Base):
    __tablename__='source_records'
    __table_args__=(UniqueConstraint('source_id','external_id','sha256',name='uq_source_external_hash'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey('source_registry.id'))
    external_id: Mapped[str] = mapped_column(String(180))
    raw_payload: Mapped[dict] = mapped_column(JSON)
    sha256: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(String(30), default='v1')
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Company(Base):
    __tablename__='companies'
    id: Mapped[int] = mapped_column(primary_key=True)
    registration: Mapped[str|None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    resolution_status: Mapped[str] = mapped_column(String(40), default='pending_review')
    source_record_id: Mapped[int|None] = mapped_column(ForeignKey('source_records.id'), nullable=True)

class Organization(Base):
    __tablename__='organizations'
    id: Mapped[int] = mapped_column(primary_key=True)
    official_id: Mapped[str|None] = mapped_column(String(90), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(300), index=True)

class Tender(Base):
    __tablename__='tenders'
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(180), unique=True)
    title: Mapped[str] = mapped_column(String(600), index=True)
    organization_id: Mapped[int|None] = mapped_column(ForeignKey('organizations.id'), nullable=True)
    awardee_company_id: Mapped[int|None] = mapped_column(ForeignKey('companies.id'), nullable=True)
    budget_amount: Mapped[float|None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default='MNT')
    bid_count: Mapped[int|None] = mapped_column(Integer, nullable=True)
    procurement_method: Mapped[str|None] = mapped_column(String(80), nullable=True)
    published_at: Mapped[str|None] = mapped_column(String(30), nullable=True)
    source_record_id: Mapped[int] = mapped_column(ForeignKey('source_records.id'))

class TenderPlan(Base):
    __tablename__='tender_plans'
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(600), index=True)
    tender_code: Mapped[str|None] = mapped_column(String(180), nullable=True, index=True)
    tender_year: Mapped[int|None] = mapped_column(Integer, nullable=True, index=True)
    budget_amount: Mapped[float|None] = mapped_column(Float, nullable=True)
    organization_name: Mapped[str|None] = mapped_column(String(300), nullable=True)
    source_record_id: Mapped[int] = mapped_column(ForeignKey('source_records.id'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class TenderSupplier(Base):
    __tablename__='tender_suppliers'
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(240), unique=True, index=True)
    invitation_id: Mapped[str] = mapped_column(String(180), index=True)
    supplier_name: Mapped[str] = mapped_column(String(300))
    supplier_register: Mapped[str|None] = mapped_column(String(80), nullable=True, index=True)
    offered_price: Mapped[float|None] = mapped_column(Float, nullable=True)
    status: Mapped[str|None] = mapped_column(String(160), nullable=True)
    comment: Mapped[str|None] = mapped_column(Text, nullable=True)
    source_record_id: Mapped[int] = mapped_column(ForeignKey('source_records.id'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class Relationship(Base):
    __tablename__='relationships'
    id: Mapped[int] = mapped_column(primary_key=True)
    from_type: Mapped[str] = mapped_column(String(40))
    from_id: Mapped[int] = mapped_column(Integer)
    to_type: Mapped[str] = mapped_column(String(40))
    to_id: Mapped[int] = mapped_column(Integer)
    relationship_type: Mapped[str] = mapped_column(String(100))
    source_record_id: Mapped[int] = mapped_column(ForeignKey('source_records.id'))
    confidence: Mapped[float] = mapped_column(Float, default=1)
    verification_status: Mapped[str] = mapped_column(String(40), default='pending_review')
    valid_from: Mapped[str|None] = mapped_column(String(32), nullable=True)
    valid_to: Mapped[str|None] = mapped_column(String(32), nullable=True)
    reviewer_note: Mapped[str|None] = mapped_column(Text, nullable=True)

class Evidence(Base):
    __tablename__='evidence_items'
    id: Mapped[int] = mapped_column(primary_key=True)
    source_record_id: Mapped[int] = mapped_column(ForeignKey('source_records.id'))
    source_url: Mapped[str] = mapped_column(Text)
    document_hash: Mapped[str] = mapped_column(String(64))
    excerpt: Mapped[str] = mapped_column(Text)
    page_number: Mapped[int|None] = mapped_column(Integer, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)

class RiskFinding(Base):
    __tablename__='risk_findings'
    __table_args__=(UniqueConstraint('rule_code','entity_type','entity_id',name='uq_risk_entity_rule'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_code: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(250))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(20), default='low')
    confidence: Mapped[float] = mapped_column(Float)
    explanation: Mapped[str] = mapped_column(Text)
    limitations: Mapped[str] = mapped_column(Text)
    evidence_id: Mapped[int] = mapped_column(ForeignKey('evidence_items.id'))
    review_status: Mapped[str] = mapped_column(String(30), default='pending_review')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class ReviewAction(Base):
    __tablename__='review_actions'
    id: Mapped[int] = mapped_column(primary_key=True)
    risk_id: Mapped[int] = mapped_column(ForeignKey('risk_findings.id'))
    reviewer: Mapped[str] = mapped_column(String(100))
    decision: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class AuditLog(Base):
    __tablename__='audit_logs'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
