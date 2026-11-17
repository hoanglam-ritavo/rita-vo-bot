"""Opt-in preprovisioned disposable HRM fixture; no test DDL, seed or cleanup writes."""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.database.config import validate_url
from src.database.runtime import run_async
from src.employee_source import EmployeeSourceService, EmployeeSourceSettings
from src.employee_source.connection import ReadOnlySource

pytestmark = pytest.mark.integration


@pytest.fixture
def hrm_fixture_settings():
    value = os.environ.get('TEST_HRM_DATABASE_URL')
    if not value:
        pytest.skip('Set TEST_HRM_DATABASE_URL to a preprovisioned disposable readonly HRM fixture.')
    if os.environ.get('TEST_HRM_DISPOSABLE_CONFIRMED') != 'true':
        pytest.fail('TEST_HRM_DISPOSABLE_CONFIRMED=true is required for the isolated fixture.')
    url = validate_url(value, 'TEST_HRM_DATABASE_URL')
    for name in ('DATABASE_URL', 'MIGRATION_DATABASE_URL', 'HRM_DATABASE_URL', 'TEST_DATABASE_URL'):
        existing = os.environ.get(name)
        if existing:
            other = validate_url(existing, name)
            if (url.host, url.port or 5432, url.database) == (other.host, other.port or 5432, other.database):
                pytest.fail('TEST_HRM_DATABASE_URL must name a separate disposable database.')
    return EmployeeSourceSettings(url, os.environ.get('TEST_HRM_DATABASE_SCHEMA', 'public'))


def test_preprovisioned_projection_reads_and_missing(hrm_fixture_settings):
    async def exercise():
        service = EmployeeSourceService(hrm_fixture_settings)
        try:
            assert (await service.check_source())['readonly'] is True
            result = await service.find_many_by_user_ids([910001, 910002, 910099])
            assert set(result) == {910001, 910002}
            assert result[910001].employee_name == 'Synthetic HRM Fixture'
            assert result[910001].official_date == '2020-01-02'
            # Fixture includes an older active and a newer inactive row for 910002.
            assert result[910002].active is False
            assert await service.find_by_user_id(910099) is None
        finally:
            await service.close()
    run_async(exercise())


def test_postgresql_rejects_write_even_with_empty_target(hrm_fixture_settings):
    async def exercise():
        source = ReadOnlySource(hrm_fixture_settings)
        relation = f'"{hrm_fixture_settings.schema}"."employee_sso_store"'
        try:
            # Test-only write probes have no target rows and must still be rejected.
            for statement in (f'UPDATE {relation} SET active = active WHERE false',
                              f'DELETE FROM {relation} WHERE false'):
                with pytest.raises(DBAPIError) as error:
                    async with source._read_scope() as connection:
                        await connection.execute(text(statement))
                assert getattr(error.value.orig, 'sqlstate', None) in ('25006', '42501')
        finally:
            await source.close()
    run_async(exercise())
