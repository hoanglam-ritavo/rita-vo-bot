"""Run-owned v3 evidence; authored JSON retains exact source semantics."""
from uuid import UUID
from decimal import Decimal
from sqlalchemy import ForeignKey, ForeignKeyConstraint, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from src.database.base import Base


class EvidenceScope(Base):
    __tablename__ = 'evidence_scopes'
    run_id: Mapped[UUID] = mapped_column(ForeignKey('ingestion_runs.id'), primary_key=True)
    author_id: Mapped[str] = mapped_column(Text, primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB)


class EvidenceItem(Base):
    __tablename__ = 'evidence_items'
    run_id: Mapped[UUID] = mapped_column(ForeignKey('ingestion_runs.id'), primary_key=True)
    author_id: Mapped[str] = mapped_column(Text, primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB)


class EvidenceValue(Base):
    __tablename__ = 'evidence_values'
    run_id: Mapped[UUID] = mapped_column(ForeignKey('ingestion_runs.id'), primary_key=True)
    author_id: Mapped[str] = mapped_column(Text, primary_key=True)
    item_id: Mapped[str] = mapped_column(Text)
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric)
    data: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (ForeignKeyConstraint(['run_id', 'item_id'], ['evidence_items.run_id', 'evidence_items.author_id']),)
