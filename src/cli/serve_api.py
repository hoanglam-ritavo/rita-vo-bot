"""Run the local API on the existing psycopg-compatible event loop."""
import logging
import sys

from src.database.runtime import run_async


async def main():
    import uvicorn
    from src.answering.config import load_settings
    from src.api.app import create_app
    try:
        settings = load_settings()
        server = uvicorn.Server(uvicorn.Config(create_app(settings), host=settings.host,
                                port=settings.port, workers=1, access_log=False, lifespan='on'))
        await server.serve()
        return 0 if server.started else 1
    except Exception:
        print('API failed. Check local configuration, models and port availability.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    try:
        raise SystemExit(run_async(main()))
    except KeyboardInterrupt:
        raise SystemExit(0)
