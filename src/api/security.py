"""One service credential for the HTTP boundary; no resource initialization."""
import hmac
from starlette.responses import JSONResponse
from starlette.datastructures import Headers


def protected(path):
    return path == '/api/v1' or path.startswith('/api/v1/') or path in ('/health/ready', '/health/answer')


class ServiceAuthenticationMiddleware:
    """Pure ASGI: do not wrap receive or hide disconnects from owned workers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        root = scope.get('root_path', '')
        if root and (path == root or path.startswith(root + '/')):
            path = path[len(root):]
        if scope['type'] == 'http' and protected(path):
            owner = getattr(scope['app'].state, 'retrieval_service', None)
            expected = getattr(getattr(owner, 'settings', None), 'service_key', None)
            supplied = Headers(scope=scope).getlist('X-Rita-Service-Key')
            valid = (isinstance(expected, str) and len(supplied) == 1 and
                     hmac.compare_digest(supplied[0].encode('utf-8'), expected.encode('utf-8')))
            if not valid:
                response = JSONResponse(status_code=401, headers={'Cache-Control': 'no-store'}, content={
                    'error': {'code': 'service_authentication_required', 'message': 'Yêu cầu xác thực dịch vụ.'}})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
