from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


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
