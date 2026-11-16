"""Execution tracking is separate from committed publication snapshots."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from src.database.base import Base


class PublicationAttempt(Base):
    __tablename__ = 'publication_attempts'
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    declared_revision_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, server_default='running')
    outcome: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey('ingestion_runs.id'), index=True)
    __table_args__ = (
        CheckConstraint("status IN ('running','succeeded','failed','cancelled')", name='status'),
        CheckConstraint("outcome IS NULL OR outcome IN ('published','unchanged','metadata_updated')", name='outcome'),
        CheckConstraint("(outcome IS NULL AND run_id IS NULL) OR (outcome IS NOT NULL AND run_id IS NOT NULL)", name='receipt'),
        CheckConstraint("(status = 'running' AND finished_at IS NULL) OR (status <> 'running' AND finished_at IS NOT NULL)", name='terminal_time'),
        CheckConstraint("status <> 'succeeded' OR (outcome IS NOT NULL AND error_code IS NULL)", name='success'),
        CheckConstraint("status NOT IN ('failed','cancelled') OR (outcome IS NULL AND error_code IS NOT NULL)", name='failure'),
        CheckConstraint('finished_at IS NULL OR started_at <= finished_at', name='times'),
    )
