from typing import Literal
from uuid import UUID
from fastapi import Header, Query, Request, Response
from src.api.contracts import ApiError
from .contracts import RetryRequest, Identity, TurnResponse


def register_routes(app):
    def service(request, key):
        current = request.app.state.conversation_service
        current.require_enabled()
        return current

    def identifier(value):
        return str(value)

    @app.post('/api/v1/conversations/{room_id}/turns/{message_id}/retry', response_model=TurnResponse)
    async def retry(room_id: UUID, message_id: UUID, body: RetryRequest, request: Request, response: Response,
                    key: str = Header(alias='X-Rita-Service-Key')):
        from src.api.app import execute_until_disconnect
        response.headers['Cache-Control'] = 'no-store'
        result = await execute_until_disconnect(request, service(request, key).retry(
            identifier(room_id), identifier(message_id), body))
        response.status_code = 202 if result.state == 'PROCESSING' else 200
        return result

    @app.get('/api/v1/conversations/{room_id}/turns/{message_id}', response_model=TurnResponse)
    async def turn(room_id: UUID, message_id: UUID, user_id: UUID, request: Request, response: Response,
                   key: str = Header(alias='X-Rita-Service-Key')):
        response.headers['Cache-Control'] = 'no-store'
        return await service(request, key).turn(str(room_id), str(user_id), str(message_id))

    @app.get('/api/v1/conversations/{room_id}/messages')
    async def messages(room_id: UUID, user_id: UUID, request: Request, response: Response,
                       after: int | None = Query(default=None, ge=0, le=2**53-1),
                       limit: int = Query(default=20, ge=1, le=50),
                       order: Literal['asc', 'desc'] = 'asc',
                       before: int | None = Query(default=None, ge=1, le=2**53-1),
                       key: str = Header(alias='X-Rita-Service-Key')):
        response.headers['Cache-Control'] = 'no-store'
        if (order == 'asc' and before is not None) or (order == 'desc' and after is not None):
            raise ApiError('invalid_history_cursor', 400)
        return await service(request, key).messages(str(room_id), str(user_id), after, limit,
                                                    order=order, before=before)

    @app.post('/api/v1/conversations/{room_id}/purge')
    async def purge(room_id: UUID, body: Identity, request: Request, response: Response,
                    key: str = Header(alias='X-Rita-Service-Key')):
        response.headers['Cache-Control'] = 'no-store'
        await service(request, key).purge(str(room_id), body.user_id)
        return {'status': 'deleted'}
