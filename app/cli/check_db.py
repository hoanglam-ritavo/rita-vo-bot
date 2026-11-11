import sys

from app.database import check_database, close_database
from app.database.config import ConfigurationError
from app.database.runtime import run_async
from app.database.session import DatabaseCheckError


async def main() -> int:
    try:
        version = await check_database()
        print(f'Database OK; pgvector {version}.')
        return 0
    except (ConfigurationError, DatabaseCheckError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print('Database check failed. Check connectivity, credentials and TLS settings.', file=sys.stderr)
        return 1
    finally:
        await close_database()


if __name__ == '__main__':
    raise SystemExit(run_async(main()))
