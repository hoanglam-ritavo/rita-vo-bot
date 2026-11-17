"""Explicitly invoked personnel reads, without business routing or eligibility decisions."""
from types import MappingProxyType
from collections.abc import Mapping

from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout
from src.database.config import ConfigurationError

from .config import EmployeeSourceSettings
from .connection import ReadOnlySource
from .contracts import EmployeeProfile, MAX_ID, normalize_profile
from .errors import EmployeeSourceError, EmployeeValidationError
from .repository import EmployeeRepository
from .selector import select_rows


def _validate_id(value):
    if type(value) is not int or not 0 < value <= MAX_ID:
        raise EmployeeValidationError('invalid_identity')


def _database_error(error):
    state = getattr(getattr(error, 'orig', None), 'sqlstate', None)
    return EmployeeSourceError('source_timeout' if state == '57014' or isinstance(error, PoolTimeout) else
                               'schema_incompatible' if state in ('42P01', '42703') else
                               'source_access_denied' if state == '42501' else 'source_unavailable')


class EmployeeSourceService:
    def __init__(self, settings: EmployeeSourceSettings | None = None):
        self._source = ReadOnlySource(settings)

    async def find_by_user_id(self, user_id: int) -> EmployeeProfile | None:
        _validate_id(user_id)
        return (await self.find_many_by_user_ids([user_id])).get(user_id)

    async def find_many_by_user_ids(self, user_ids: list[int] | tuple[int, ...]) -> Mapping[int, EmployeeProfile]:
        if not isinstance(user_ids, (list, tuple)) or len(user_ids) > 200:
            raise EmployeeValidationError('invalid_batch')
        for user_id in user_ids:
            _validate_id(user_id)
        ids = set(user_ids)
        if not ids:
            return MappingProxyType({})
        try:
            repository = EmployeeRepository(self._source._configure().schema)
            async with self._source._read_scope() as connection:
                rows = await repository._read_by_ids(connection, ids)
                profiles = [normalize_profile(row) for row in select_rows(rows, ids)]
                employee_ids = [profile.employee_id for profile in profiles]
                if len(set(employee_ids)) != len(employee_ids):
                    raise EmployeeValidationError('invalid_identity')
                return MappingProxyType({profile.user_id: profile for profile in profiles})
        except EmployeeSourceError:
            raise
        except (DBAPIError, PoolTimeout) as error:
            raise _database_error(error) from None
        except Exception as error:
            if isinstance(error, ConfigurationError):
                raise
            raise EmployeeSourceError('source_unavailable') from None

    async def check_source(self):
        try:
            repository = EmployeeRepository(self._source._configure().schema)
            async with self._source._read_scope() as connection:
                return await repository._check(connection)
        except EmployeeSourceError:
            raise
        except (DBAPIError, PoolTimeout) as error:
            raise _database_error(error) from None
        except Exception as error:
            if isinstance(error, ConfigurationError):
                raise
            raise EmployeeSourceError('source_unavailable') from None

    async def close(self):
        try:
            await self._source.close()
        except Exception:
            raise EmployeeSourceError('source_cleanup_failed') from None
