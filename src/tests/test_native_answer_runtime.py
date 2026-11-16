"""Native model ownership/cancellation contracts using a fictional binding, no weights."""
import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

from src.answering.config import from_environment
from src.answering.identity import BINDING_VERSION
from src.answering.runtime import LocalRuntime, grammar_schema
from src.answering.contracts import Draft, validate_draft
import src.answering.runtime as runtime_module


@pytest.fixture
def native(tmp_path, monkeypatch):
    state = SimpleNamespace(threads=[], reset_count=0, close_count=0, load_count=0,
        tokens=20, prompt='formatted conversation', calls=[], grammar=None, format_args=None,
        response={'choices': [{'finish_reason': 'stop', 'text': '{"ok":true}'}],
                  'usage': {'completion_tokens': 5, 'private_text': 'PRIVATE'}}, action=None)
    def thread():
        state.threads.append(threading.get_ident())
    class Model:
        def __init__(self, **kwargs):
            thread()
            state.load_count += 1
            state.load_kwargs = kwargs
            self.metadata = {'tokenizer.chat_template': 'fictional template'}
            self._ctx = SimpleNamespace(ctx=object())
        def token_eos(self): return 1
        def token_bos(self): return -1
        def detokenize(self, *args, **kwargs): return b'<eos>'
        def tokenize(self, *args, **kwargs):
            thread()
            state.tokenize_kwargs = kwargs
            return list(range(state.tokens))
        def reset(self):
            thread()
            state.reset_count += 1
        def create_completion(self, **kwargs):
            thread()
            state.calls.append(kwargs)
            if state.action:
                state.action(kwargs)
            return state.response
        def close(self):
            thread()
            state.close_count += 1
    class Formatter:
        def __init__(self, *args, **kwargs):
            thread()
        def __call__(self, **kwargs):
            thread()
            state.format_args = kwargs
            return SimpleNamespace(prompt=state.prompt, stop=['<eos>'])
    def grammar(schema, **kwargs):
        state.grammar = json.loads(schema)
        return state.grammar
    def register_abort(ctx, callback, data):
        state.abort = callback
    binding = SimpleNamespace(__version__=BINDING_VERSION, Llama=Model,
        LlamaGrammar=SimpleNamespace(from_json_schema=grammar), StoppingCriteriaList=list,
        llama_chat_format=SimpleNamespace(Jinja2ChatFormatter=Formatter),
        llama_cpp=SimpleNamespace(llama_log_callback=lambda fn: fn, llama_log_set=lambda *a: None,
            ggml_abort_callback=lambda fn: fn, llama_set_abort_callback=register_abort))
    monkeypatch.setattr(runtime_module, 'import_module', lambda _: binding)
    runtime = LocalRuntime(tmp_path / 'model.gguf', 'fictional-hash')
    monkeypatch.setattr(runtime, 'verify_files', lambda: {'model': 'fictional-hash'})
    return runtime, state


def test_native_model_lifecycle_grammar_and_single_thread(native):
    runtime, state = native
    main_thread = threading.get_ident()
    async def run():
        await runtime.start()
        await runtime.start()
        result, usage = await runtime.complete('policy', {'question': 'Fictional'}, {'type': 'object'}, 512)
        assert result == {'ok': True} and usage == {'completion_tokens': 5}
        assert state.grammar == {'type': 'object'}
        assert state.format_args['enable_thinking'] is False
        assert state.format_args['messages'][0] == {'role': 'system', 'content': 'policy'}
        assert state.tokenize_kwargs == {'add_bos': False, 'special': True}
        assert state.calls[0]['prompt'] == list(range(20))  # Count and inference share exact token IDs.
        assert state.calls[0]['max_tokens'] == 512 and state.calls[0]['stream'] is False
        assert state.load_kwargs['n_ctx'] == 8192 and state.load_kwargs['n_gpu_layers'] == 0
        assert state.load_kwargs['n_threads'] == 6
        await runtime.complete('other policy', {}, {}, 32)
        assert state.reset_count == 4
        await runtime.close()
        await runtime.close()
    asyncio.run(run())
    assert state.load_count == state.close_count == 1
    assert len(set(state.threads)) == 1 and main_thread not in state.threads


@pytest.mark.parametrize('failure', ['context', 'thinking_prompt', 'thinking_output', 'incomplete', 'malformed_json'])
def test_context_and_unexpected_output_are_rejected(native, failure):
    runtime, state = native
    if failure == 'context': state.tokens = 8192
    elif failure == 'thinking_prompt': state.prompt = '<think>'
    elif failure == 'thinking_output': state.response['choices'][0]['text'] = '<think>PRIVATE</think>{"ok":true}'
    elif failure == 'incomplete': state.response['choices'][0]['finish_reason'] = 'length'
    else: state.response['choices'][0]['text'] = 'PRIVATE invalid JSON'
    async def run():
        await runtime.start()
        try:
            with pytest.raises(ValueError):
                await runtime.complete('policy', {}, {}, 512)
            if failure in ('context', 'thinking_prompt'):
                assert not state.calls
        finally:
            await runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['prefill', 'generation'])
def test_native_cancellation_drains_before_free_and_keeps_event_loop_responsive(native, phase):
    runtime, state = native
    entered, cancelled_seen, release = threading.Event(), threading.Event(), threading.Event()
    def work(kwargs):
        entered.set()
        while True:
            stopped = state.abort(None) if phase == 'prefill' else kwargs['stopping_criteria'][0](None, None)
            if stopped:
                cancelled_seen.set()
                release.wait(timeout=3)
                raise RuntimeError('PRIVATE native error')
            time.sleep(.001)
    state.action = work
    async def wait_for(event):
        while not event.is_set(): await asyncio.sleep(.001)
    async def run():
        await runtime.start()
        task = asyncio.create_task(runtime.complete('policy', {}, {}, 32))
        await asyncio.wait_for(wait_for(entered), 2)
        task.cancel()
        await asyncio.wait_for(wait_for(cancelled_seen), 2)
        task.cancel()
        closing = asyncio.create_task(runtime.close())
        await asyncio.sleep(.01)
        assert state.close_count == 0 and not task.done() and not closing.done()
        pending = asyncio.create_task(runtime.complete('other question', {}, {}, 32))
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
        await closing
        with pytest.raises(ValueError, match='unavailable'):
            await pending
        assert state.close_count == 1
        assert len(state.calls) == 1
    asyncio.run(run())


def test_native_deadline_uses_abort_callback(native):
    runtime, state = native
    def work(kwargs):
        while not state.abort(None): time.sleep(.001)
        raise RuntimeError('decode aborted')
    state.action = work
    async def run():
        await runtime.start()
        try:
            with pytest.raises(TimeoutError):
                await runtime._execute(lambda event, deadline:
                    runtime._complete('policy', {}, {}, 32, event, deadline), seconds=.01)
        finally: await runtime.close()
    asyncio.run(run())


def test_failed_load_closes_partial_model(native):
    runtime, state = native
    original = runtime._load
    def fail(*args):
        original(*args)
        raise ValueError('fictional load failure')
    runtime._load = fail
    async def run():
        with pytest.raises(ValueError): await runtime.start()
        assert state.close_count == 1 and runtime._executor is None
        await runtime.close()
    asyncio.run(run())


def test_retired_server_environment_is_ignored():
    config = from_environment({'ANSWER_PORT': 'invalid', 'ANSWER_RUNTIME_PATH': 'missing',
                               'ANSWER_RUNTIME_SHA256': 'old', 'ANSWER_ENABLED': 'false'})
    assert config.enabled is False
    assert not hasattr(config, 'port') and not hasattr(config, 'runtime_path')


def test_large_string_limits_remain_in_validation_without_recursive_native_grammar():
    schema = Draft.model_json_schema()
    compiled = grammar_schema(schema)
    original = schema['$defs']['Claim']['properties']['text']
    assert original['maxLength'] == 2000
    assert 'maxLength' not in compiled['$defs']['Claim']['properties']['text']
    assert compiled['$defs']['Claim']['properties']['text']['minLength'] == 1
    assert compiled['properties']['claims']['maxItems'] == 12
    raw = {'status': 'answered', 'claims': [{'text': 'x' * 2001,
        'citations': [{'source_id': 'S1', 'quote': 'fictional'}]}], 'limitations': []}
    with pytest.raises(ValueError):
        validate_draft(raw, {'S1': {'content': 'fictional'}})
