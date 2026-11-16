from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from src.database.base import Base


class IngestionRun(Base):
    __tablename__ = 'ingestion_runs'
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    revision_id: Mapped[UUID] = mapped_column(ForeignKey('document_revisions.id'), index=True)
    status: Mapped[str] = mapped_column(Text, server_default='succeeded')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    source_sha256: Mapped[str] = mapped_column(Text)
    configuration: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    model_name: Mapped[str] = mapped_column(Text)
    model_revision: Mapped[str] = mapped_column(Text)
    embedding_dimensions: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, server_default='0')
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text('false'))
    __table_args__ = (
        CheckConstraint("status = 'succeeded'", name='status'),
        CheckConstraint("NOT is_active OR status = 'succeeded'", name='active_success'),
        CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name='sha256'),
        CheckConstraint('chunk_count >= 0', name='chunk_count'),
        CheckConstraint('embedding_dimensions = 384', name='dimensions'),
        CheckConstraint("length(trim(model_name)) > 0", name='model_name'),
        CheckConstraint("model_revision ~ '^[0-9a-f]{40}$'", name='model_revision'),
        CheckConstraint("jsonb_typeof(configuration) = 'object'", name='configuration_object'),
        CheckConstraint("configuration->>'model_files_sha256' IS NOT NULL AND configuration->>'model_files_sha256' ~ '^[0-9a-f]{64}$' AND configuration->>'content_hash' IS NOT NULL AND configuration->>'content_hash' ~ '^[0-9a-f]{64}$' AND configuration->>'package_sha256' IS NOT NULL AND configuration->>'package_sha256' ~ '^[0-9a-f]{64}$' AND configuration->>'metadata_sha256' IS NOT NULL AND configuration->>'metadata_sha256' ~ '^[0-9a-f]{64}$'", name='fingerprints'),
        Index('uq_run_active_revision', 'revision_id', unique=True, postgresql_where=text('is_active')),
    )
