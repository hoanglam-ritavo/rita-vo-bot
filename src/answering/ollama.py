"""Local-only Ollama client. Never pulls models or follows redirects."""
import httpx
from pydantic import ValidationError
from .contracts import AnswerError, ModelAnswer


class OllamaClient:
    def __init__(self, settings):
        self.settings = settings
        self.client = httpx.AsyncClient(base_url=settings.ollama_url, timeout=120,
                                       trust_env=False, follow_redirects=False)
        self.digest = None
        self.version = None

    async def request(self, method, path, **kwargs):
        try:
            response = await self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            raise AnswerError('ollama_timeout', 504) from None
        except (httpx.HTTPError, ValueError):
            raise AnswerError('ollama_unavailable') from None

    async def check(self):
        data = await self.request('GET', '/api/tags')
        models = data.get('models', []) if isinstance(data, dict) else []
        item = next((m for m in models if isinstance(m, dict) and m.get('name') == self.settings.llm_model), None)
        if not item or item.get('remote_model') or item.get('remote_host') or not item.get('digest'):
            raise AnswerError('local_llm_missing')
        info = await self.request('POST', '/api/show', json={'model': self.settings.llm_model})
        if not isinstance(info, dict) or info.get('remote_model') or info.get('remote_host'):
            raise AnswerError('remote_llm_rejected')
        if self.digest is not None and self.digest != item['digest']:
            raise AnswerError('llm_model_changed')
        self.digest = item['digest']

    async def start(self):
        await self.check()
        version = await self.request('GET', '/api/version')
        self.version = version.get('version')
        await self.generate([{'role': 'user', 'content': 'Return an unsupported answer: this is a startup check, no evidence is available.'}], timeout=300)

    async def generate(self, messages, timeout=120):
        data = await self.request('POST', '/api/chat', timeout=timeout, json={
            'model': self.settings.llm_model, 'messages': messages,
            'stream': False, 'think': False, 'keep_alive': -1,
            'format': ModelAnswer.model_json_schema(),
            'options': {'num_ctx': 8192, 'num_predict': 1024, 'temperature': 0},
        })
        try:
            if data.get('done') is not True or data.get('done_reason') != 'stop':
                raise ValueError()
            return ModelAnswer.model_validate_json(data['message']['content'])
        except (AttributeError, KeyError, TypeError, ValueError, ValidationError):
            raise AnswerError('invalid_model_output', 502) from None

    async def close(self):
        await self.client.aclose()
