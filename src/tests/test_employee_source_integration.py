"""Opt-in preprovisioned disposable HRM fixture; no test DDL, seed or cleanup writes."""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.database.runtime import run_async
from src.tests.database_targets import require_disposable_database
from src.type_b.employee_store import EmployeeSourceService, EmployeeSourceSettings
from src.type_b.employee_store.connection import ReadOnlySource

pytestmark = pytest.mark.integration


@pytest.fixture
def hrm_fixture_settings():
    url = require_disposable_database('TEST_HRM_DATABASE_URL')
    if os.environ.get('TEST_HRM_DISPOSABLE_CONFIRMED') != 'true':
        pytest.fail('TEST_HRM_DISPOSABLE_CONFIRMED=true is required for the isolated fixture.')
    return EmployeeSourceSettings(url, os.environ.get('TEST_HRM_DATABASE_SCHEMA', 'public'))


def test_preprovisioned_projection_reads_and_missing(hrm_fixture_settings):
    async def exercise():
        service = EmployeeSourceService(hrm_fixture_settings)
        try:
            assert (await service.check_source())['readonly'] is True
            current = await service.read_context(910001, ('job_title',))
            assert current is not None
            assert dict(current.values) == {'job_title': 'Synthetic Engineer'}
            inactive = await service.read_context(910002, ('job_title', 'active'))
            assert inactive is not None
            assert dict(inactive.values) == {'job_title': None, 'active': False}
            assert await service.read_context(910099, ('job_title',)) is None

            result = await service.find_many_by_user_ids([910001, 910002, 910099])
            assert set(result) == {910001, 910002}
            assert result[910001].employee_name == 'Synthetic HRM Fixture'
            assert result[910001].official_date == '2020-01-02'
            # Fixture includes an older active and a newer inactive row for 910002.
            assert result[910002].active is False
            assert await service.find_by_user_id(910099) is None
            employee_id = result[910001].employee_id
            assert await service.find_by_employee_id(employee_id) == result[910001]
            assert dict(await service.find_many_by_employee_ids([employee_id, employee_id])) == {
                employee_id: result[910001]}
        finally:
            await service.close()
    run_async(exercise())


def test_preprovisioned_manager_relations_and_unlinked_employee(hrm_fixture_settings):
    async def exercise():
        service = EmployeeSourceService(hrm_fixture_settings)
        try:
            results = await service.find_managers_by_user_ids(list(range(910003, 910011)) + [910099])
            expected = {
                910003: 'found', 910004: 'found', 910005: 'manager_not_found',
                910006: 'conflicting_link', 910007: 'ambiguous',
                910008: 'manager_not_found', 910009: 'self_reference',
                910010: 'missing_link', 910099: 'employee_not_found',
            }
            assert {uid: result.status for uid, result in results.items()} == expected
            for uid in (910003, 910004):
                manager = results[uid].manager
                assert manager.employee_id == 920001 and manager.user_id is None
                assert manager.work_email == 'manager@example.test' and manager.active is False
            assert (await service.find_by_employee_id(920001)).user_id is None
        finally:
            await service.close()
    run_async(exercise())


def test_postgresql_rejects_write_even_with_empty_target(hrm_fixture_settings):
    async def exercise():
        source = ReadOnlySource(hrm_fixture_settings)
        relation = f'"{hrm_fixture_settings.schema}"."employee_sso_store"'
        try:
            async with source._read_scope() as connection:
                can_write = await connection.scalar(text('''
                    SELECT has_table_privilege(:relation, 'UPDATE')
                       AND has_table_privilege(:relation, 'DELETE')
                '''), {'relation': relation})
                assert can_write is True, 'Grant UPDATE and DELETE on the synthetic fixture table.'
            # Test-only write probes have no target rows and must still be rejected.
            for statement in (f'UPDATE {relation} SET active = active WHERE false',
                              f'DELETE FROM {relation} WHERE false'):
                with pytest.raises(DBAPIError) as error:
                    async with source._read_scope() as connection:
                        await connection.execute(text(statement))
                assert getattr(error.value.orig, 'sqlstate', None) == '25006'
        finally:
            await source.close()
    run_async(exercise())
