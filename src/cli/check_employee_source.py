"""Metadata-only HRM preflight. No employee rows, migrations or model loading."""
import sys

from dotenv import load_dotenv

from src.database.config import ConfigurationError
from src.database.runtime import run_async
from src.type_b.employee_store import EmployeeSourceError, EmployeeSourceService
from src.paths import REPOSITORY_ROOT


async def main():
    service = EmployeeSourceService()
    code = 0
    try:
        load_dotenv(REPOSITORY_ROOT / '.env', override=False)
        await service.check_source()
    except (ConfigurationError, EmployeeSourceError) as error:
        print(str(error), file=sys.stderr)
        code = 1
    except Exception:
        print('Employee source check failed.', file=sys.stderr)
        code = 1
    finally:
        try:
            await service.close()
        except Exception:
            print('Employee source cleanup failed.', file=sys.stderr)
            code = 1
    if code == 0:
        print('Employee source OK; readonly transaction and 64-column projection checked.')
    return code


if __name__ == '__main__':
    raise SystemExit(run_async(main()))
