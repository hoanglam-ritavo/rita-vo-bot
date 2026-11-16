"""Run-owned v3 evidence; authored JSON retains exact source semantics."""
from uuid import UUID
from decimal import Decimal
from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from src.database.base import Base


class EvidenceScope(Base):
    __tablename__ = 'evidence_scopes'
    run_id: Mapped[UUID] = mapped_column(ForeignKey('ingestion_runs.id'), primary_key=True)
    author_id: Mapped[str] = mapped_column(Text, primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        CheckConstraint("jsonb_typeof(data) = 'object' AND data->>'id' IS NOT NULL AND data->>'id' = author_id", name='snapshot'),
        CheckConstraint("author_id ~ '^[a-zA-Z0-9_.-]{1,100}$'", name='author_id'),
    )


class EvidenceItem(Base):
    __tablename__ = 'evidence_items'
    run_id: Mapped[UUID] = mapped_column(ForeignKey('ingestion_runs.id'), primary_key=True)
    author_id: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(['run_id', 'parent_id'], ['evidence_items.run_id', 'evidence_items.author_id'], deferrable=True, initially='DEFERRED'),
        CheckConstraint('parent_id IS NULL OR parent_id <> author_id', name='not_self'),
        CheckConstraint("jsonb_typeof(data) = 'object' AND data->>'id' IS NOT NULL AND data->>'id' = author_id AND (data->>'parent_id') IS NOT DISTINCT FROM parent_id", name='snapshot'),
        CheckConstraint("author_id ~ '^[a-zA-Z0-9_.-]{1,100}$'", name='author_id'),
    )


class EvidenceValue(Base):
    __tablename__ = 'evidence_values'
    run_id: Mapped[UUID] = mapped_column(ForeignKey('ingestion_runs.id'), primary_key=True)
    author_id: Mapped[str] = mapped_column(Text, primary_key=True)
    item_id: Mapped[str] = mapped_column(Text)
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric)
    data: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(['run_id', 'item_id'], ['evidence_items.run_id', 'evidence_items.author_id']),
        CheckConstraint("author_id ~ '^[a-zA-Z0-9_.-]{1,100}$'", name='author_id'),
        CheckConstraint("jsonb_typeof(data) = 'object' AND data->>'id' IS NOT NULL AND data->>'id' = author_id AND data->>'item_id' IS NOT NULL AND data->>'item_id' = item_id", name='snapshot'),
        CheckConstraint("data->>'type' IS NOT NULL AND data->>'type' IN ('text','boolean','decimal','date')", name='value_type'),
        CheckConstraint("CASE WHEN data->>'type' = 'decimal' THEN numeric_value IS NOT NULL AND data->>'value' IS NOT NULL AND data->>'value' ~ '^-?[0-9]+([.][0-9]+)?$' AND numeric_value = (data->>'value')::numeric ELSE numeric_value IS NULL END", name='numeric_projection'),
    )


class ScopeItem(Base):
    __tablename__ = 'evidence_scope_items'
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    scope_id: Mapped[str] = mapped_column(Text, primary_key=True)
    item_id: Mapped[str] = mapped_column(Text, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        ForeignKeyConstraint(['run_id', 'scope_id'], ['evidence_scopes.run_id', 'evidence_scopes.author_id']),
        ForeignKeyConstraint(['run_id', 'item_id'], ['evidence_items.run_id', 'evidence_items.author_id']),
        UniqueConstraint('run_id', 'scope_id', 'position'),
        CheckConstraint('position >= 0', name='position'),
    )


class ScopeChunk(Base):
    __tablename__ = 'evidence_scope_chunks'
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    scope_id: Mapped[str] = mapped_column(Text, primary_key=True)
    chunk_id: Mapped[str] = mapped_column(Text, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        ForeignKeyConstraint(['run_id', 'scope_id'], ['evidence_scopes.run_id', 'evidence_scopes.author_id']),
        ForeignKeyConstraint(['run_id', 'chunk_id'], ['document_chunks.run_id', 'document_chunks.author_id']),
        UniqueConstraint('run_id', 'scope_id', 'position'),
        CheckConstraint('position >= 0', name='position'),
    )


class ItemChunk(Base):
    __tablename__ = 'evidence_item_chunks'
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    item_id: Mapped[str] = mapped_column(Text, primary_key=True)
    chunk_id: Mapped[str] = mapped_column(Text, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        ForeignKeyConstraint(['run_id', 'item_id'], ['evidence_items.run_id', 'evidence_items.author_id']),
        ForeignKeyConstraint(['run_id', 'chunk_id'], ['document_chunks.run_id', 'document_chunks.author_id']),
        UniqueConstraint('run_id', 'item_id', 'position'),
        CheckConstraint('position >= 0', name='position'),
    )
