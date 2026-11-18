"""Add isolated conversation storage; no corpus/HRM changes.

Revision ID: d8e031c72b19
Revises: c7d92e6a104b
"""
from alembic import op

revision = 'd8e031c72b19'
down_revision = 'c7d92e6a104b'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE chat_conversations (
        room_id UUID NOT NULL,
        owner_id UUID,
        deleted BOOLEAN DEFAULT 'false' NOT NULL,
        version BIGINT DEFAULT '0' NOT NULL,
        sequence BIGINT DEFAULT '0' NOT NULL,
        checkpoint_version BIGINT DEFAULT '0' NOT NULL,
        checkpoint JSONB DEFAULT '{}' NOT NULL,
        CONSTRAINT pk_chat_conversations PRIMARY KEY (room_id)
)''')
    op.execute('''CREATE TABLE chat_turns (
        room_id UUID NOT NULL,
        message_id UUID NOT NULL,
        sequence BIGINT NOT NULL,
        fingerprint VARCHAR(64) NOT NULL,
        payload JSONB NOT NULL,
        state VARCHAR(16) NOT NULL,
        attempt INTEGER NOT NULL,
        context_version BIGINT NOT NULL,
        context_delta JSONB,
        result JSONB,
        error_code VARCHAR(64),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT pk_chat_turns PRIMARY KEY (room_id, message_id),
        CONSTRAINT fk_chat_turns_room_id_chat_conversations FOREIGN KEY(room_id) REFERENCES chat_conversations (room_id) ON DELETE CASCADE,
        CONSTRAINT uq_chat_turns_room_id UNIQUE (room_id, sequence),
        CONSTRAINT ck_chat_turns_turn_state CHECK (state IN ('PROCESSING','COMPLETED','FAILED','CANCELLED','INTERRUPTED')),
        CONSTRAINT ck_chat_turns_turn_result CHECK ((state = 'COMPLETED') = (result IS NOT NULL))
)''')
    op.execute('''CREATE TABLE chat_messages (
        room_id UUID NOT NULL,
        message_id UUID NOT NULL,
        role VARCHAR(16) NOT NULL,
        content TEXT NOT NULL,
        CONSTRAINT pk_chat_messages PRIMARY KEY (room_id, message_id, role),
        CONSTRAINT fk_chat_messages_room_id_chat_turns FOREIGN KEY(room_id, message_id) REFERENCES chat_turns (room_id, message_id) ON DELETE CASCADE,
        CONSTRAINT ck_chat_messages_message_role CHECK (role IN ('user','assistant'))
)''')
    op.execute("CREATE INDEX ix_chat_turns_room_context ON chat_turns(room_id, context_version) WHERE state = 'COMPLETED'")
    op.execute("CREATE UNIQUE INDEX uq_chat_turns_room_processing ON chat_turns(room_id) WHERE state = 'PROCESSING'")


def downgrade():
    # Explicit opt-in downgrade destroys chat data, including deletion receipts.
    for table in ('chat_messages', 'chat_turns', 'chat_conversations'):
        op.execute(f'DROP TABLE {table}')
