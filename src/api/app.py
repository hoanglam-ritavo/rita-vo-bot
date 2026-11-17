"""FastAPI application factory; importing does not initialize runtime resources."""
import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import load_settings
from .contracts import RetrievalResponse, AnswerResponse, AnswerHealthResponse, ApiError, RetrieveRequest
from .contracts import ContextRetrievalResponse, ContextAnswerResponse
from .answer import drain
from .service import ApiService

logger = logging.getLogger(__name__)


async def execute_until_disconnect(request, operation):
    task = asyncio.create_task(operation)
    async def watch_disconnect():
        while not await request.is_disconnected():
            await asyncio.sleep(.1)
    watcher = asyncio.create_task(watch_disconnect())
    try:
        done, _ = await asyncio.wait((task, watcher), return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        raise asyncio.CancelledError
    finally:
        watcher.cancel()
        if not task.done():
            task.cancel()
        async def cleanup():
            await asyncio.gather(task, watcher, return_exceptions=True)
        await drain(asyncio.create_task(cleanup()))


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
            401: 'Yêu cầu chưa được xác thực từ dịch vụ nội bộ.',
            422: 'Câu hỏi vượt giới hạn xử lý. Vui lòng rút gọn và gửi lại đủ ngữ cảnh.',
            429: 'Dịch vụ đang xử lý yêu cầu khác. Vui lòng thử lại sau.',
            503: 'Dịch vụ hoặc nguồn dữ liệu tạm thời không khả dụng. Vui lòng thử lại sau.',
            504: 'Yêu cầu vượt thời gian xử lý. Vui lòng thử lại hoặc chia nhỏ câu hỏi.',
        }.get(exc.status_code, 'Không thể xử lý yêu cầu lúc này. Vui lòng thử lại sau.')
        if exc.code == 'hrm_user_id_required':
            message = 'Cần tham chiếu hrm_user_id do backend xác thực để tra cứu thông tin công việc.'
        return JSONResponse(status_code=exc.status_code, headers={'Cache-Control': 'no-store'},
                            content={'error': {'code': exc.code, 'message': message}})

    @app.exception_handler(RequestValidationError)
    async def input_error(request, exc):
        # Do not echo the submitted text in validation errors.
        return JSONResponse(status_code=422, headers={'Cache-Control': 'no-store'}, content={'error': {'code': 'invalid_request', 'message': 'text phải là chuỗi không rỗng, tối đa 8.000 ký tự; top_k là số nguyên từ 1 đến 20; hrm_user_id là số nguyên dương hoặc null.'}})

    @app.post('/api/v1/retrieve', response_model=RetrievalResponse | ContextRetrievalResponse,
        description='Retrieve at most top_k source excerpts. With RELEVANCE_FILTER_ENABLED=true (default), '
                    'missing calibration returns an explicit empty fallback. When false, candidates are unassessed; '
                    'scores and relevance metadata do not certify confidence or complete evidence. '
                    'Optional hrm_user_id enables readonly work-profile evidence when Type B is configured; '
                    'requires X-Rita-Service-Key and backend authorization.', responses={
        401: {'description': 'Trusted service authentication required for personnel context.'},
        422: {'description': 'Invalid text, top-K or model token limit exceeded.'},
        429: {'description': 'Another retrieval is in progress.'},
        503: {'description': 'Dependency unavailable or incompatible model.'},
        504: {'description': 'Retrieval or dependency timed out.'},
    })
    async def retrieve(body: RetrieveRequest, request: Request, response: Response,
                       service_key: str | None = Header(default=None, alias='X-Rita-Service-Key',
                           description='Internal service credential; never a user authorization token.')):
        service = request.app.state.retrieval_service
        top_k = body.top_k if 'top_k' in body.model_fields_set else service.settings.top_k
        result = await execute_until_disconnect(request, service.execute(body.text, top_k, body.hrm_user_id,
            service_key))
        if body.hrm_user_id is not None or isinstance(result, ContextRetrievalResponse):
            response.headers['Cache-Control'] = 'no-store'
        return result

    @app.post('/api/v1/answer', response_model=AnswerResponse | ContextAnswerResponse,
              description='Opt-in source-bound Semantic + template answers, with explicit excerpt fallback. '
                          'Optional hrm_user_id supports readonly work-profile facts through a trusted backend. '
                          'No generative model, conversation history or individual policy eligibility decisions.',
              responses={401: {'description': 'Trusted service authentication required.'},
                         422: {'description': 'Invalid request or required HRM reference missing.'}, 429: {'description': 'Service busy.'},
                         503: {'description': 'Answering disabled, catalog incompatible or dependencies unavailable.'},
                         504: {'description': 'Answer deadline exceeded.'}})
    async def answer(body: RetrieveRequest, request: Request, response: Response,
                     service_key: str | None = Header(default=None, alias='X-Rita-Service-Key',
                         description='Internal service credential; never a user authorization token.')):
        service = request.app.state.retrieval_service
        top_k = body.top_k if 'top_k' in body.model_fields_set else service.settings.top_k
        result = await execute_until_disconnect(request, service.execute(body.text, top_k, body.hrm_user_id,
            service_key, answer=True))
        if body.hrm_user_id is not None or isinstance(result, ContextAnswerResponse):
            response.headers['Cache-Control'] = 'no-store'
        return result

    @app.get('/health/live')
    async def live():
        return {'status': 'alive'}

    @app.get('/health/ready')
    async def ready(request: Request):
        await request.app.state.retrieval_service.ready()
        return {'status': 'ready', 'notice': 'Availability only; retrieval quality and dataset completeness are not verified.'}

    @app.get('/health/answer', response_model=AnswerHealthResponse,
             description='Answer initialization and dependency availability only; not answer quality or completeness.',
             responses={503: {'model': AnswerHealthResponse, 'description': 'Answering or dependencies unavailable.'}})
    async def answer_ready(request: Request):
        reason = await request.app.state.retrieval_service.answer_readiness()
        body = AnswerHealthResponse(status='unavailable' if reason else 'ready', reason=reason)
        return JSONResponse(status_code=503 if reason else 200, content=body.model_dump())

    return app
