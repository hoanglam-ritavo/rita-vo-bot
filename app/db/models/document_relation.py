from uuid import UUID
from sqlalchemy import CheckConstraint, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class DocumentRelation(Base):
    __tablename__ = 'document_relations'
    source_revision_id: Mapped[UUID] = mapped_column(ForeignKey('document_revisions.id'), primary_key=True)
    target_revision_id: Mapped[UUID] = mapped_column(ForeignKey('document_revisions.id'), primary_key=True, index=True)
    relation_type: Mapped[str] = mapped_column(Text, primary_key=True)
    __table_args__ = (
        CheckConstraint('source_revision_id <> target_revision_id', name='not_self'),
        CheckConstraint("relation_type IN ('duplicate_of','supplements','supersedes','form_for')", name='type'),
    )
