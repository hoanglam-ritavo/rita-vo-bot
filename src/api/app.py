"""FastAPI application factory; importing does not initialize runtime resources."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import load_settings
from .contracts import RetrievalResponse, ApiError, RetrieveRequest
from .service import ApiService

logger = logging.getLogger(__name__)


def create_app(settings=None, *, service_factory=ApiService):
    @asynccontextmanager
    async def lifespan(app):
        service = service_factory(settings or load_settings())
        app.state.retrieval_service = service
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

    app = FastAPI(title='Rita local document retrieval', lifespan=lifespan)

    @app.exception_handler(ApiError)
    async def retrieval_error(request, exc):
        logger.warning('retrieval_error code=%s', exc.code)
        message = {
            422: 'Câu hỏi vượt giới hạn xử lý. Vui lòng rút gọn và gửi lại đủ ngữ cảnh.',
            429: 'Dịch vụ đang xử lý yêu cầu khác. Vui lòng thử lại sau.',
            503: 'Dịch vụ hoặc nguồn dữ liệu tạm thời không khả dụng. Vui lòng thử lại sau.',
            504: 'Yêu cầu vượt thời gian xử lý. Vui lòng thử lại hoặc chia nhỏ câu hỏi.',
        }.get(exc.status_code, 'Không thể xử lý yêu cầu lúc này. Vui lòng thử lại sau.')
        return JSONResponse(status_code=exc.status_code,
                            content={'error': {'code': exc.code, 'message': message}})

    @app.exception_handler(RequestValidationError)
    async def input_error(request, exc):
        # Do not echo the submitted text in validation errors.
        return JSONResponse(status_code=422, content={'error': {'code': 'invalid_request', 'message': 'text phải là chuỗi không rỗng, tối đa 8.000 ký tự; top_k là số nguyên từ 1 đến 20.'}})

    @app.post('/api/v1/retrieve', response_model=RetrievalResponse,
        description='Retrieve at most top_k relevance-filtered excerpts. Missing calibration returns an explicit empty fallback; '
                    'scores and relevance metadata do not certify confidence or complete evidence.', responses={
        422: {'description': 'Invalid text, top-K or model token limit exceeded.'},
        429: {'description': 'Another retrieval is in progress.'},
        503: {'description': 'Dependency unavailable or incompatible model.'},
        504: {'description': 'Retrieval or dependency timed out.'},
    })
    async def retrieve(body: RetrieveRequest, request: Request):
        service = request.app.state.retrieval_service
        top_k = body.top_k if 'top_k' in body.model_fields_set else service.settings.top_k
        return await service.retrieve(body.text, top_k)

    @app.get('/health/live')
    async def live():
        return {'status': 'alive'}

    @app.get('/health/ready')
    async def ready(request: Request):
        await request.app.state.retrieval_service.ready()
        return {'status': 'ready', 'notice': 'Availability only; retrieval quality and dataset completeness are not verified.'}

    return app
