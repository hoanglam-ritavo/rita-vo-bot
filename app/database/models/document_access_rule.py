from uuid import UUID
from sqlalchemy import CheckConstraint, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database.base import Base


class DocumentAccessRule(Base):
    __tablename__ = 'document_access_rules'
    revision_id: Mapped[UUID] = mapped_column(ForeignKey('document_revisions.id'), primary_key=True)
    group_name: Mapped[str] = mapped_column(Text, primary_key=True)
    __table_args__ = (CheckConstraint("length(trim(group_name)) > 0", name='group_nonempty'),)
