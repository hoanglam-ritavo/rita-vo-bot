"""Internal readonly personnel service. No routing, identity authorization or policy decisions."""
import asyncio
from contextlib import asynccontextmanager
from types import MappingProxyType
from collections.abc import Mapping

from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout
from src.database.config import ConfigurationError

from .config import EmployeeSourceSettings
from .connection import ReadOnlySource
from .contracts import EmployeeProfile, ManagerResult
from .fields import MAX_ID, MAX_BATCH, WORK_FIELDS, FIELD_REGISTRY, RANK_TEXT_FIELDS
from .normalization import normalize_profile, normalize_manager, normalize_values, identity, text_value
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
        self._closing = False
        self._close_task = None
        self._active = 0
        self._idle = asyncio.Event()
        self._idle.set()

    def _ids(self, values):
        if self._closing:
            raise EmployeeSourceError('source_closed')
        if not isinstance(values, (list, tuple)) or len(values) > MAX_BATCH:
            raise EmployeeValidationError('invalid_batch')
        for value in values:
            _validate_id(value)
        return tuple(dict.fromkeys(values))

    @asynccontextmanager
    async def _read(self):
        if self._closing:
            raise EmployeeSourceError('source_closed')
        self._active += 1
        self._idle.clear()
        try:
            repository = EmployeeRepository(self._source._configure().schema)
            async with self._source._read_scope() as connection:
                yield repository, connection
        except EmployeeSourceError:
            raise
        except (DBAPIError, PoolTimeout) as error:
            raise _database_error(error) from None
        except ConfigurationError:
            raise
        except Exception:
            raise EmployeeSourceError('source_unavailable') from None
        finally:
            self._active -= 1
            if not self._active:
                self._idle.set()

    async def find_by_user_id(self, user_id: int) -> EmployeeProfile | None:
        return (await self.find_many_by_user_ids([user_id])).get(user_id)

    async def find_many_by_user_ids(self, user_ids: list[int] | tuple[int, ...]) -> Mapping[int, EmployeeProfile]:
        return await self._find_many(user_ids, 'user_id')

    async def find_by_employee_id(self, employee_id: int) -> EmployeeProfile | None:
        return (await self.find_many_by_employee_ids([employee_id])).get(employee_id)

    async def find_many_by_employee_ids(self, employee_ids: list[int] | tuple[int, ...]) -> Mapping[int, EmployeeProfile]:
        return await self._find_many(employee_ids, 'employee_id')

    async def _find_many(self, values, key):
        ids = self._ids(values)
        if not ids:
            return MappingProxyType({})
        async with self._read() as (repository, connection):
            rows = await repository._read_by_ids(connection, ids, key=key)
            profiles = [normalize_profile(row) for row in select_rows(rows, ids, key=key)]
            employee_ids = [profile.employee_id for profile in profiles]
            if len(set(employee_ids)) != len(employee_ids):
                raise EmployeeValidationError('invalid_identity')
            return MappingProxyType({getattr(profile, key): profile for profile in profiles})

    async def find_manager_by_user_id(self, user_id: int) -> ManagerResult:
        return (await self.find_managers_by_user_ids([user_id]))[user_id]

    async def find_managers_by_user_ids(self, user_ids: list[int] | tuple[int, ...]) -> Mapping[int, ManagerResult]:
        ids = self._ids(user_ids)
        if not ids:
            return MappingProxyType({})
        async with self._read() as (repository, connection):
            rows = await repository._read_by_ids(connection, ids, work_only=True)
            selected = select_rows(rows, ids, projection=WORK_FIELDS)
            return await self._resolve_managers(repository, connection, selected, ids)

    async def _resolve_managers(self, repository, connection, selected, ids):
        subjects, links = {}, {}
        for row in selected:
            subject = normalize_manager(row)
            if 'manager_id' not in row or 'manager_code' not in row:
                raise EmployeeValidationError('invalid_projection')
            manager_id = None if row['manager_id'] is None else identity(row['manager_id'])
            subjects[subject.user_id] = subject
            links[subject.user_id] = (manager_id, text_value(row['manager_code']))
        if len({s.employee_id for s in subjects.values()}) != len(subjects):
            raise EmployeeValidationError('invalid_identity')
        manager_ids = {value[0] for value in links.values() if value[0] is not None}
        codes = {code for mid, code in links.values() if mid is None and code is not None}
        by_id, by_code = {}, {}
        if manager_ids:
            rows = await repository._read_by_ids(connection, manager_ids, key='employee_id', work_only=True)
            by_id = {m.employee_id: m for m in (
                normalize_manager(row) for row in select_rows(
                    rows, manager_ids, key='employee_id', projection=WORK_FIELDS))}
        if codes:
            rows = await repository._read_code_candidates(connection, codes)
            candidates = {identity(row.get('employee_id')) for row in rows}
            for row in select_rows(rows, candidates, key='employee_id', projection=WORK_FIELDS):
                manager = normalize_manager(row)
                if manager.employee_code in codes:
                    by_code.setdefault(manager.employee_code, []).append(manager)
        results = {}
        for uid in ids:
            if uid not in subjects:
                results[uid] = ManagerResult('employee_not_found')
                continue
            mid, code = links[uid]
            if mid is None and code is None:
                result = ManagerResult('missing_link')
            elif mid is not None:
                manager = by_id.get(mid)
                if manager is None:
                    result = ManagerResult('manager_not_found')
                elif code is not None and manager.employee_code != code:
                    result = ManagerResult('conflicting_link')
                else:
                    result = self._manager_result(subjects[uid], manager)
            else:
                candidates = by_code.get(code, [])
                if len(candidates) > 1:
                    result = ManagerResult('ambiguous')
                elif not candidates:
                    result = ManagerResult('manager_not_found')
                else:
                    result = self._manager_result(subjects[uid], candidates[0])
            results[uid] = result
        return MappingProxyType(results)

    async def read_context(self, user_id: int, fields: tuple[str, ...], *, manager: bool = False):
        """One authorized subject, one snapshot, named fields only; never a browser field API."""
        from datetime import datetime, timezone
        from .contracts import EmployeeContext
        self._ids([user_id])
        if type(fields) is not tuple or any(type(field) is not str for field in fields) or type(manager) is not bool or not set(fields) <= set(FIELD_REGISTRY) or 'user_id' in fields or 'account_id' in fields or 'id' in fields:
            raise EmployeeValidationError('invalid_projection')
        projection = tuple(dict.fromkeys((*(WORK_FIELDS if manager else
            ('user_id', 'employee_id', 'updated_at', 'active', *RANK_TEXT_FIELDS)), *fields)))
        async with self._read() as (repository, connection):
            rows = await repository._read_by_ids(connection, [user_id], fields=projection)
            selected = select_rows(rows, [user_id], projection=projection)
            if not selected:
                return None
            values = normalize_values(selected[0], projection)
            relation = (await self._resolve_managers(repository, connection, selected, [user_id]))[user_id] if manager else None
            return EmployeeContext(MappingProxyType({key: values[key] for key in fields}), relation,
                datetime.now(timezone.utc).isoformat(), values['updated_at'])

    @staticmethod
    def _manager_result(subject, manager):
        if subject.employee_id == manager.employee_id or (
                subject.user_id is not None and subject.user_id == manager.user_id):
            return ManagerResult('self_reference')
        return ManagerResult('found', manager)

    async def check_source(self) -> dict:
        async with self._read() as (repository, connection):
            return await repository._check(connection)

    async def _finish_close(self):
        await self._idle.wait()
        try:
            await self._source.close()
        except Exception:
            raise EmployeeSourceError('source_cleanup_failed') from None

    async def close(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._finish_close())
        # A cancelled closer must not abandon disposal or cancel active reads.
        await asyncio.shield(self._close_task)
