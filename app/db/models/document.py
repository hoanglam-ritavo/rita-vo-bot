from uuid import UUID, uuid4
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.models.mixins import Timestamps


class Document(Timestamps, Base):
    __tablename__ = 'documents'
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
