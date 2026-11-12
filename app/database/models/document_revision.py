from datetime import date
from uuid import UUID, uuid4
from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from app.database.base import Base
from app.database.models.mixins import Timestamps


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
    indexing_decision: Mapped[str] = mapped_column(Text, server_default='hold')
    evidence_notes: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    is_catalog_current: Mapped[bool] = mapped_column(Boolean, server_default=text('false'))
    __table_args__ = (
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name='sha256'),
        CheckConstraint('size_bytes >= 0', name='size_bytes'),
        CheckConstraint('effective_from <= effective_to', name='effective_dates'),
        CheckConstraint("lifecycle_status IN ('unknown','active','superseded','withdrawn','expired')", name='lifecycle'),
        CheckConstraint("indexing_decision IN ('hold','include','exclude')", name='indexing'),
        CheckConstraint("jsonb_typeof(evidence_notes) = 'array'", name='notes_array'),
        Index('uq_revision_current_document', 'document_id', unique=True, postgresql_where=text('is_catalog_current')),
        Index('uq_revision_current_path', 'source_path_key', unique=True, postgresql_where=text('is_catalog_current')),
    )
