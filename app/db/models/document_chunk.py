from uuid import UUID, uuid4
from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


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
