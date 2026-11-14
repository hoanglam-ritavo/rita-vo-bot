import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
import pytest
from src.retrieval import RetrievalService, RetrievalError
from src.retrieval import storage
from src.ingestion import embeddings
from src.database.runtime import run_async
from src.cli import retrieve_documents as cli
from src.retrieval import runner


@pytest.fixture
def model(monkeypatch):
    tokenizer = Mock(return_value={'input_ids': [1] * 5})
    tokenizer.model_max_length = 8
    model = SimpleNamespace(max_seq_length=5, tokenizer=tokenizer,
        encode=Mock(return_value=[SimpleNamespace(tolist=lambda: [2.0] + [0.0] * 383)]))
    monkeypatch.setattr(embeddings, 'load_model', Mock(return_value=model))
    monkeypatch.setattr(embeddings, 'model_fingerprint', Mock(return_value='fingerprint'))
    monkeypatch.setattr(storage, 'search', AsyncMock(return_value={'status': 'matches_found', 'matches': []}))
    return model


def service():
    return RetrievalService(model_path='unused', model_revision='a' * 40)


def test_reuses_model_and_query_prefix(model):
    async def scenario():
        s = service()
        await s.start()
        await s.start()
        await s.retrieve('  policy?  ')
        await s.retrieve('another question')
        await s.close()
        assert s._model is None
    run_async(scenario())
    embeddings.load_model.assert_called_once()
    assert model.tokenizer.call_args_list[0].args == ('query: policy?',)
    assert model.tokenizer.call_args.kwargs == {'add_special_tokens': True, 'truncation': False}
    assert model.encode.call_args.kwargs['normalize_embeddings'] is True
    assert storage.search.call_args.args[0] == [1.0] + [0.0] * 383


@pytest.mark.parametrize('question,top_k,code', [('', 5, 'invalid_question'), ('  ', 5, 'invalid_question'),
    (None, 5, 'invalid_question'), (42, 5, 'invalid_question'), ('a' * 8001, 5, 'question_too_long'),
    ('ok', 0, 'invalid_top_k'), ('ok', 21, 'invalid_top_k'), ('ok', True, 'invalid_top_k')])
def test_input_before_model(question, top_k, code):
    with pytest.raises(RetrievalError, match=code):
        run_async(service().retrieve(question, top_k))


@pytest.mark.parametrize('vector', [[0.0] * 384, [1.0], [float('nan')] * 384, [float('inf')] * 384])
def test_invalid_query_vector(model, vector):
    model.encode.return_value = [SimpleNamespace(tolist=lambda: vector)]
    async def scenario():
        s = service()
        await s.start()
        with pytest.raises(RetrievalError, match='invalid_query_vector'):
            await s.retrieve('question')
        await s.close()
    run_async(scenario())
    storage.search.assert_not_called()


def test_token_limit(model):
    model.tokenizer.return_value = {'input_ids': [1] * 6}
    async def scenario():
        s = service()
        await s.start()
        with pytest.raises(RetrievalError, match='question_exceeds_model_limit'):
            await s.retrieve('question')
        await s.close()
    run_async(scenario())
    model.encode.assert_not_called()


@pytest.mark.parametrize('stage,code', [('load', 'model_load_failed'), ('encode', 'query_embedding_failed'),
                                      ('db', 'database_search_failed')])
def test_safe_operational_errors(model, stage, code):
    target = {'load': embeddings.load_model, 'encode': model.encode, 'db': storage.search}[stage]
    target.side_effect = RuntimeError('secret')
    async def scenario():
        s = service()
        with pytest.raises(RetrievalError) as info:
            await s.start()
            await s.retrieve('question')
        assert info.value.code == code and info.value.exit_code == 2
        await s.close()
    run_async(scenario())


def test_model_change_at_start(model):
    embeddings.model_fingerprint.side_effect = ['first', 'second']
    with pytest.raises(RetrievalError, match='model_changed'):
        run_async(service().start())


def test_serial_model_access_and_cancellation(model):
    import threading
    entered, release = threading.Event(), threading.Event()
    original = model.encode.return_value
    def encode(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original
    model.encode.side_effect = encode
    async def scenario():
        s = service()
        await s.start()
        request = asyncio.create_task(s.retrieve('question'))
        assert await asyncio.to_thread(entered.wait, 5)
        request.cancel()
        close = asyncio.create_task(s.close())
        await asyncio.sleep(0)
        assert not close.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        await close
    run_async(scenario())


@pytest.mark.parametrize('status,code', [('matches_found', 0), ('no_matches', 0),
    ('no_eligible_documents', 0), ('model_mismatch', 1)])
def test_cli_json_status_and_no_vectors(model, capsys, status, code):
    storage.search.return_value = {'status': status, 'matches': [], 'notice': 'Candidates only'}
    assert cli.main(['--question', 'policy?', '--model-path', 'unused', '--model-revision', 'a'*40, '--json']) == code
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == ('no_matches' if status == 'matches_found' else status)
    assert 'embedding' not in report


def test_cli_operational_failure_is_safe(model, capsys):
    model.encode.side_effect = RuntimeError('secret credential')
    assert cli.main(['--question', 'policy?', '--model-path', 'unused', '--model-revision', 'a'*40, '--json']) == 2
    output = capsys.readouterr()
    assert 'secret' not in output.out + output.err
    assert json.loads(output.out)['code'] == 'query_embedding_failed'


def test_cli_invalid_input_before_load(model, capsys):
    assert cli.main(['--question', 'policy?', '--model-path', 'unused', '--model-revision', 'a'*40,
                     '--top-k', '0', '--json']) == 1
    assert json.loads(capsys.readouterr().out)['code'] == 'invalid_top_k'
    embeddings.load_model.assert_not_called()


def test_import_has_no_runtime_side_effects():
    script = '''import sys
import src.retrieval, src.cli.retrieve_documents
assert not any(m in sys.modules for m in ('sentence_transformers', 'sqlalchemy', 'src.database', 'dotenv'))
'''
    result = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('stage,status', [
    ('success', 'matches_found'), ('success', 'no_matches'),
    ('success', 'no_eligible_documents'), ('success', 'model_mismatch'),
    ('input', 'error'), ('load', 'error'), ('retrieve', 'error'),
    ('close', 'error'), ('database_close', 'error'),
])
def test_runner_completed_timings_exclude_other_work(monkeypatch, stage, status):
    import src.database
    clock = [10.0]
    monkeypatch.setattr(runner, 'time', SimpleNamespace(perf_counter=lambda: clock[0]))
    original_validation = runner.validate_request
    def validate(question, top_k):
        clock[0] += 100
        return original_validation(question, top_k)
    monkeypatch.setattr(runner, 'validate_request', validate)
    class FakeService:
        def __init__(self, **kwargs):
            clock[0] += 200
        async def start(self):
            clock[0] += 12.34567
            if stage == 'load':
                raise RetrievalError('model_load_failed', 2)
        async def retrieve(self, *args):
            clock[0] += 0.123456
            if stage == 'retrieve':
                raise RetrievalError('database_search_failed', 2)
            return {'status': status, 'matches': [{'content': 'Synthetic content'}]}
        async def close(self):
            clock[0] += 300
            if stage == 'close':
                raise RuntimeError('safe test')
    async def close_database():
        clock[0] += 400
        if stage == 'database_close':
            raise RuntimeError('safe test')
    monkeypatch.setattr(runner, 'RetrievalService', FakeService)
    monkeypatch.setattr(src.database, 'close_database', close_database)
    args = SimpleNamespace(question='' if stage == 'input' else 'question', top_k=5,
                           model_path='unused', model_revision='a'*40)
    result, code = run_async(cli.run(args))
    timings = result['timings']
    if stage in ('input', 'load'):
        assert timings['model_initialization_seconds'] is None
    else:
        assert timings['model_initialization_seconds'] == pytest.approx(12.34567)
    if stage in ('input', 'load', 'retrieve'):
        assert timings['retrieval_seconds'] is None
    else:
        assert timings['retrieval_seconds'] == pytest.approx(0.123456)
    assert code == (1 if stage == 'input' or status == 'model_mismatch' else 0 if stage == 'success' else 2)
    if stage == 'success':
        assert result['matches'] == [{'content': 'Synthetic content'}]
    if stage in ('close', 'database_close'):
        assert result['code'] == 'shutdown_failed'


def test_cli_run_delegates_to_shared_runner(monkeypatch):
    args = SimpleNamespace(question='Synthetic question')
    expected = ({'status': 'no_matches', 'matches': []}, 0)
    shared = AsyncMock(return_value=expected)
    monkeypatch.setattr(runner, 'run', shared)
    assert run_async(cli.run(args)) == expected
    shared.assert_awaited_once_with(args)
