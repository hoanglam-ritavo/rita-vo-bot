"""Opt-in UUID-schema PostgreSQL checks configured through the process environment.

Set TEST_CONVERSATION_DATABASE_URL to an explicitly disposable PostgreSQL URL.
The fixture creates/drops only its own random schema and uses the chat migration.
"""
import asyncio
from contextlib import asynccontextmanager
import importlib
from multiprocessing import get_context
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.api.contracts import ApiError
from src.conversation.contracts import ConversationRequest, ConversationSettings, RetryRequest
from src.conversation.service import ConversationService
from src.conversation import storage
from src.database.runtime import run_async
from src.helper.cache import CacheManager
from src.tests.database_targets import require_disposable_database
from src.tests.test_conversation import catalog, result

pytestmark = pytest.mark.integration


def _conversation_process(url, schema, pipe, payloads, *, crash_last=False, retry_last=False):
    async def scenario():
        engine = create_async_engine(url, hide_parameters=True, echo=False,
            connect_args={'options': f'-csearch_path={schema}'})
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        @asynccontextmanager
        async def scope():
            async with sessions.begin() as session:
                yield session

        storage.session_scope = scope
        plans = []

        async def execute(text, top_k, hrm_user_id, *, analysis, **kwargs):
            if crash_last and text == 'Còn hồ sơ thì sao?':
                # Inference begins only after the PROCESSING reservation commits.
                pipe.send('reserved')
                await asyncio.Event().wait()
            plans.append((analysis.question, analysis.aspects, analysis.user_inputs))
            return SimpleNamespace(model_dump=lambda **_: result())

        owner = SimpleNamespace(settings=SimpleNamespace(conversation=ConversationSettings(True)),
            busy=False, _idle=asyncio.Event(),
            answers=SimpleNamespace(catalog=catalog(), lexicon={'concepts': []}),
            context=SimpleNamespace(execute=execute))
        cache = CacheManager()
        service = ConversationService(owner, cache)
        try:
            await service.start()
            for payload in payloads:
                request = ConversationRequest.model_validate(payload)
                if retry_last:
                    interrupted = await service.turn(request.room_id, request.user_id, request.message_id)
                    assert interrupted.state == 'INTERRUPTED'
                    assert interrupted.context_version == request.expected_context_version
                    assert not plans
                    done = await service.retry(request.room_id, request.message_id,
                        RetryRequest(user_id=request.user_id, hrm_user_id=request.hrm_user_id))
                    assert done.attempt == 2 and done.message_id == request.message_id
                else:
                    done = await service.answer(request)
                assert done.state == 'COMPLETED'
                assert done.context_version == request.expected_context_version + 1
            pipe.send(plans)
        finally:
            await service.close()
            await cache.close()
            await engine.dispose()

    with pipe:
        run_async(scenario())


def _run_worker(url, schema, payloads, *, crash=False, retry=False):
    multiprocessing = get_context('spawn')
    receive, sender = multiprocessing.Pipe(duplex=False)
    worker = multiprocessing.Process(target=_conversation_process, args=(url, schema, sender, payloads),
        kwargs={'crash_last': crash, 'retry_last': retry})
    with receive, sender:
        worker.start()
        sender.close()
        try:
            assert receive.poll(45), 'Conversation worker did not report within 45 seconds'
            outcome = receive.recv()
            if crash:
                assert outcome == 'reserved'
                worker.kill()
            worker.join(10)
            assert not worker.is_alive()
            if not crash:
                assert worker.exitcode == 0
            return outcome
        finally:
            if worker.is_alive():
                worker.kill()
                worker.join(10)
            worker.close()


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
                        assert await store.context(room, owner) == ({'version': 4}, 'snapshot')
                        assert await store.context(room, owner, {'version': 3, 'state': {}}) == (
                            {'version': 4}, 'snapshot')
                        assert await store.context(room, owner, {'version': 4, 'state': {'cached': True}}) == (
                            {'cached': True}, 'hit')
                assert await store.context(room, owner) == ({'version': 8}, 'checkpoint')
                with pytest.raises(ApiError):
                    await store.begin(failed, retry=True)
                page, _ = await store.messages(room, owner, limit=2)
                assert [t['sequence'] for t in page['items']] == [1, 2]
                assert page['next_cursor'] == 2
                seen, before = [], None
                while True:
                    page, _ = await store.messages(room, owner, limit=2, order='desc', before=before)
                    seen.extend(t['sequence'] for t in page['items'])
                    assert page['context_version'] == 8
                    before = page['next_cursor']
                    if before is None:
                        break
                assert seen == list(range(9, 0, -1))
                large_room = str(uuid4())
                for version in range(3):
                    turn_request = req(version).model_copy(update={'room_id': large_room})
                    await store.begin(turn_request)
                    await store.finish(turn_request, 1, {'answer': 'x' * 1_000_000}, {})
                before = None
                for sequence in (3, 2, 1):
                    page, used = await store.messages(large_room, owner, order='desc', before=before)
                    assert [item['sequence'] for item in page['items']] == [sequence]
                    assert 1_000_000 < used < 1_900_000
                    before = page['next_cursor']
                    assert before == (sequence if sequence > 1 else None)
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
                with pytest.raises(ApiError):
                    await store.messages(room, str(uuid4()), order='desc')
                await store.purge(room, owner)
                await store.purge(room, owner)
                large_page, _ = await store.messages(large_room, owner, order='desc')
                assert large_page['context_version'] == 3
                await store.purge(large_room, owner)
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


def test_postgres_room_continues_after_process_restart():
    url = require_disposable_database('TEST_CONVERSATION_DATABASE_URL')

    async def scenario():
        schema = 'chat_' + uuid4().hex
        engine = create_async_engine(url, hide_parameters=True, echo=False)
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))

                def migrate(sync):
                    migration = importlib.import_module('src.database.migrations.versions.d8e031c72b19_conversations')
                    with Operations.context(MigrationContext.configure(sync)):
                        migration.upgrade()

                await connection.run_sync(migrate)

            owner, checkpoint_room, snapshot_room = (str(uuid4()) for _ in range(3))

            def payload(room, version, question):
                return ConversationRequest(text=question, user_id=owner, room_id=room, message_id=str(uuid4()),
                    expected_context_version=version, hrm_user_id=7).stored_payload()

            pending = 'sinh con và kết hôn; Ngày xét: 2026-11-18'
            seed = [payload(room, n, pending if n == version - 1 else 'sinh con')
                    for room, version in [(checkpoint_room, 8), (snapshot_room, 9)] for n in range(version)]
            _run_worker(url, schema, seed)
            followups = [payload(checkpoint_room, 8, 'sinh con'), payload(snapshot_room, 9, 'sinh con')]
            plans = _run_worker(url, schema, followups)
            assert [(question, dict(inputs)) for question, _, inputs in plans] == [
                (pending, {'as_of': '2026-11-18'}), (pending, {'as_of': '2026-11-18'}),
            ]
            interrupted = payload(snapshot_room, 10, 'Còn hồ sơ thì sao?')
            _run_worker(url, schema, [interrupted], crash=True)
            plans = _run_worker(url, schema, [interrupted], retry=True)
            assert [(question, list(aspects), dict(inputs)) for question, aspects, inputs in plans] == [
                (pending, ['paperwork'], {'as_of': '2026-11-18'}),
            ]
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await engine.dispose()

    run_async(scenario())
