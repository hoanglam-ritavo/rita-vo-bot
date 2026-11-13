"""FastAPI application factory; importing does not initialize runtime resources."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.answering.config import load_settings
from src.answering.contracts import Answer, AnswerError, AskRequest
from src.answering.service import AnswerService

logger = logging.getLogger(__name__)


def create_app(settings=None, *, service_factory=AnswerService):
    @asynccontextmanager
    async def lifespan(app):
        service = service_factory(settings or load_settings())
        app.state.answer_service = service
        try:
            await service.start()
            yield
        except Exception:
            raise RuntimeError('API lifecycle failed; check local model and database configuration.') from None
        finally:
            try:
                await service.close()
            except Exception:
                raise RuntimeError('API shutdown failed.') from None

    app = FastAPI(title='Rita local document answers', lifespan=lifespan)

    @app.exception_handler(AnswerError)
    async def answer_error(request, exc):
        logger.warning('answer_error code=%s', exc.code)
        return JSONResponse(status_code=exc.status_code,
                            content={'error': {'code': exc.code, 'message': 'Không thể xử lý yêu cầu. Kiểm tra mã lỗi và cấu hình dịch vụ.'}})

    @app.exception_handler(RequestValidationError)
    async def input_error(request, exc):
        # Do not echo the submitted text in validation errors.
        return JSONResponse(status_code=422, content={'error': {'code': 'invalid_request', 'message': 'text phải là chuỗi không rỗng, tối đa 8.000 ký tự.'}})

    @app.post('/api/v1/ask', response_model=Answer, responses={
        422: {'description': 'Invalid text or model/context input limit exceeded.'},
        429: {'description': 'Another answer is in progress.'},
        502: {'description': 'Invalid LLM output or citations.'},
        503: {'description': 'Dependency unavailable or incompatible model.'},
        504: {'description': 'Answer or dependency timed out.'},
    })
    async def ask(body: AskRequest, request: Request):
        try:
            return await request.app.state.answer_service.ask(body.text)
        except AnswerError:
            raise
        except Exception:
            raise AnswerError('answer_failed', 503) from None

    @app.get('/health/live')
    async def live():
        return {'status': 'alive'}

    @app.get('/health/ready')
    async def ready(request: Request):
        await request.app.state.answer_service.ready()
        return {'status': 'ready', 'notice': 'Availability only; answer quality is not verified.'}

    return app
