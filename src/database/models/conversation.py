"""Conversation state is independent of Type A publication and the HRM source."""
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from src.database.base import Base


class Conversation(Base):
    __tablename__ = 'chat_conversations'
    room_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default='false')
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default='0')
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default='0')
    checkpoint_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default='0')
    checkpoint: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default='{}')


class ChatTurn(Base):
    __tablename__ = 'chat_turns'
    __table_args__ = (
        ForeignKeyConstraint(['room_id'], ['chat_conversations.room_id'], ondelete='CASCADE'),
        UniqueConstraint('room_id', 'sequence'),
        CheckConstraint("state IN ('PROCESSING','COMPLETED','FAILED','CANCELLED','INTERRUPTED')", name='turn_state'),
        CheckConstraint("(state = 'COMPLETED') = (result IS NOT NULL)", name='turn_result'),
        Index('ix_chat_turns_room_context', 'room_id', 'context_version', postgresql_where=text("state = 'COMPLETED'")),
        Index('uq_chat_turns_room_processing', 'room_id', unique=True, postgresql_where=text("state = 'PROCESSING'")),
    )
    room_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    message_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    context_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    context_delta: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    result: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(Base):
    __tablename__ = 'chat_messages'
    __table_args__ = (
        ForeignKeyConstraint(['room_id', 'message_id'], ['chat_turns.room_id', 'chat_turns.message_id'], ondelete='CASCADE'),
        CheckConstraint("role IN ('user','assistant')", name='message_role'),
    )
    room_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    message_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
