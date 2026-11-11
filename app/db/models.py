"""Type A metadata and future ingestion storage; imports never connect."""
from datetime import date, datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime,
                        ForeignKey, Index, Integer, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Timestamps, Base):
    __tablename__ = 'documents'
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)


class DocumentRevision(Timestamps, Base):
    __tablename__ = 'document_revisions'
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey('documents.id'), index=True)
    source_path: Mapped[str] = mapped_column(Text)
    source_path_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    extension: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    document_type: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(Text)
    applicability: Mapped[str | None] = mapped_column(Text)
    version_label: Mapped[str | None] = mapped_column(Text)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    lifecycle_status: Mapped[str] = mapped_column(Text, server_default='unknown')
    review_status: Mapped[str] = mapped_column(Text, server_default='pending')
    indexing_decision: Mapped[str] = mapped_column(Text, server_default='hold')
    review_notes: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    is_catalog_current: Mapped[bool] = mapped_column(Boolean, server_default=text('false'))
    __table_args__ = (
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name='sha256'),
        CheckConstraint('size_bytes >= 0', name='size_bytes'),
        CheckConstraint('effective_from <= effective_to', name='effective_dates'),
        CheckConstraint("lifecycle_status IN ('unknown','active','superseded','withdrawn','expired')", name='lifecycle'),
        CheckConstraint("review_status IN ('pending','reviewed')", name='review'),
        CheckConstraint("indexing_decision IN ('hold','include','exclude')", name='indexing'),
        CheckConstraint("jsonb_typeof(review_notes) = 'array'", name='notes_array'),
        Index('uq_revision_current_document', 'document_id', unique=True, postgresql_where=text('is_catalog_current')),
        Index('uq_revision_current_path', 'source_path_key', unique=True, postgresql_where=text('is_catalog_current')),
    )


class DocumentAccessRule(Base):
    __tablename__ = 'document_access_rules'
    revision_id: Mapped[UUID] = mapped_column(ForeignKey('document_revisions.id'), primary_key=True)
    group_name: Mapped[str] = mapped_column(Text, primary_key=True)
    __table_args__ = (CheckConstraint("length(trim(group_name)) > 0", name='group_nonempty'),)


class DocumentRelation(Base):
    __tablename__ = 'document_relations'
    source_revision_id: Mapped[UUID] = mapped_column(ForeignKey('document_revisions.id'), primary_key=True)
    target_revision_id: Mapped[UUID] = mapped_column(ForeignKey('document_revisions.id'), primary_key=True, index=True)
    relation_type: Mapped[str] = mapped_column(Text, primary_key=True)
    __table_args__ = (
        CheckConstraint('source_revision_id <> target_revision_id', name='not_self'),
        CheckConstraint("relation_type IN ('duplicate_of','supplements','supersedes','form_for')", name='type'),
    )


class IngestionRun(Base):
    __tablename__ = 'ingestion_runs'
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    revision_id: Mapped[UUID] = mapped_column(ForeignKey('document_revisions.id'), index=True)
    status: Mapped[str] = mapped_column(Text, server_default='pending')
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_sha256: Mapped[str] = mapped_column(Text)
    configuration: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    model_name: Mapped[str | None] = mapped_column(Text)
    model_revision: Mapped[str | None] = mapped_column(Text)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, server_default='0')
    error_code: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text('false'))
    __table_args__ = (
        CheckConstraint("status IN ('pending','running','succeeded','failed')", name='status'),
        CheckConstraint("NOT is_active OR status = 'succeeded'", name='active_success'),
        CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name='sha256'),
        CheckConstraint('chunk_count >= 0', name='chunk_count'),
        CheckConstraint('embedding_dimensions = 384', name='dimensions'),
        CheckConstraint('started_at <= finished_at', name='times'),
        CheckConstraint("jsonb_typeof(configuration) = 'object'", name='configuration_object'),
        Index('uq_run_active_revision', 'revision_id', unique=True, postgresql_where=text('is_active')),
    )


class DocumentChunk(Base):
    __tablename__ = 'document_chunks'
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('ingestion_runs.id'), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    source_location: Mapped[dict] = mapped_column(JSONB)
    extraction_method: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384))
    __table_args__ = (
        UniqueConstraint('run_id', 'chunk_index'),
        CheckConstraint('chunk_index >= 0', name='chunk_index'),
        CheckConstraint("length(trim(content)) > 0", name='content_nonempty'),
        CheckConstraint("jsonb_typeof(source_location) = 'object'", name='location_object'),
        CheckConstraint("extraction_method IN ('native','ocr')", name='extraction_method'),
    )
