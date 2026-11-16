"""Append-only metadata snapshots, not document content or execution logs."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from src.database.base import Base


class MetadataAudit(Base):
    __tablename__ = 'metadata_audits'
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    revision_id: Mapped[UUID] = mapped_column(ForeignKey('document_revisions.id'), index=True)
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey('publication_attempts.id'), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event: Mapped[str] = mapped_column(Text)
    before: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    after: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        CheckConstraint("event IN ('created','updated','superseded')", name='event'),
        CheckConstraint("before IS NULL OR jsonb_typeof(before) = 'object'", name='before_object'),
        CheckConstraint("jsonb_typeof(after) = 'object'", name='after_object'),
        CheckConstraint("(event = 'created') = (before IS NULL)", name='creation'),
        CheckConstraint('before IS DISTINCT FROM after', name='changed'),
    )
