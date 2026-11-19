"""Opt-in UUID-schema PostgreSQL checks configured through the process environment.

Set TEST_CONVERSATION_DATABASE_URL to an explicitly disposable PostgreSQL URL.
The fixture creates/drops only its own random schema and uses the chat migration.
"""
import asyncio
from contextlib import asynccontextmanager
import importlib
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.api.contracts import ApiError
from src.conversation.contracts import ConversationRequest
from src.conversation import storage
from src.database.runtime import run_async
from src.tests.database_targets import require_disposable_database

pytestmark = pytest.mark.integration


def test_postgres_turn_lifecycle_checkpoint_purge_and_constraints(monkeypatch):
    url = require_disposable_database('TEST_CONVERSATION_DATABASE_URL')

    async def scenario():
        schema = 'chat_' + uuid4().hex
        engine = create_async_engine(url, hide_parameters=True, echo=False)
        store = storage.ConversationStore()
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine_schema = create_async_engine(url, hide_parameters=True, echo=False,
                connect_args={'options': f'-csearch_path={schema}'})
            try:
                async with engine_schema.begin() as connection:
                    def migrate(sync):
                        migration = importlib.import_module('src.database.migrations.versions.d8e031c72b19_conversations')
                        with Operations.context(MigrationContext.configure(sync)):
                            migration.upgrade()
                    await connection.run_sync(migrate)
                sessions = async_sessionmaker(engine_schema, expire_on_commit=False)

                @asynccontextmanager
                async def scope():
                    async with sessions.begin() as session:
                        yield session
                monkeypatch.setattr(storage, 'session_scope', scope)
                owner, room = str(uuid4()), str(uuid4())
                def req(version=0):
                    return ConversationRequest(text='Fictional question', user_id=owner, room_id=room,
                        message_id=str(uuid4()), expected_context_version=version, hrm_user_id=7)
                first = req()
                processing, accepted = await store.begin(first)
                assert accepted and processing.state == 'PROCESSING'
                duplicate, accepted = await store.begin(first)
                assert not accepted and duplicate.state == 'PROCESSING'
                with pytest.raises(ApiError) as conflict:
                    await store.begin(first.model_copy(update={'text': 'Different'}))
                assert conflict.value.status_code == 409
                other = req()
                with pytest.raises(ApiError):
                    await store.begin(other)
                completed = await store.finish(first, 1, {'answer': 'Fictional answer'}, {'schema': 1})
                assert completed.context_version == 1
                duplicate, accepted = await store.begin(first)
                assert not accepted and duplicate.result == completed.result
                failed = req(1)
                await store.begin(failed)
                await store.fail(failed, 1, 'FAILED', 'answer_timeout')
                retry, accepted = await store.begin(failed, retry=True)
                assert accepted and retry.attempt == 2
                await store.fail(failed, 2, 'CANCELLED', 'conversation_failed')
                for version in range(1, 8):
                    current = req(version)
                    await store.begin(current)
                    await store.finish(current, 1, {'answer': 'Fictional'}, {'version': version + 1})
                    if version == 3:
                        assert await store.context(room, owner) == (4, {}, [{'version': 4}])
                version, checkpoint, deltas = await store.context(room, owner)
                assert version == 8 and checkpoint == {'version': 8} and deltas == []
                with pytest.raises(ApiError):
                    await store.begin(failed, retry=True)
                page = await store.messages(room, owner, limit=2)
                assert len(page['items']) == 2 and page['next_cursor'] is not None
                from src.conversation.contracts import ClarificationChoice
                pending = req(8).model_copy(update={'choice': ClarificationChoice(
                    prompt_message_id=first.message_id, option_id=1)})
                with pytest.raises(ApiError) as stale:
                    await store.begin(pending, choice_is_current=lambda _: False)
                assert stale.value.status_code == 409
                with pytest.raises(ApiError) as absent:
                    await store.get_turn(room, owner, pending.message_id)
                assert absent.value.status_code == 404
                await store.begin(pending, choice_is_current=lambda state: state == {'version': 8})
                await store.recover()
                assert (await store.get_turn(room, owner, pending.message_id)).state == 'INTERRUPTED'
                with pytest.raises(ApiError):
                    await store.get_turn(room, str(uuid4()), first.message_id)
                await store.purge(room, owner)
                await store.purge(room, owner)
                with pytest.raises(ApiError):
                    await store.finish(pending, 1, {'answer': 'late'}, {})
                with pytest.raises(ApiError):
                    await store.begin(req())
                async with engine_schema.connect() as connection:
                    assert await connection.scalar(text('SELECT count(*) FROM chat_messages')) == 0
                    assert await connection.scalar(text('SELECT count(*) FROM chat_turns')) == 0
                    assert await connection.scalar(text('SELECT owner_id FROM chat_conversations')) is None
            finally:
                await engine_schema.dispose()
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await engine.dispose()
    run_async(scenario())
