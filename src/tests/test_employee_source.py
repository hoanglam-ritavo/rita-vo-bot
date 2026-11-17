"""Synthetic source contracts and transaction boundaries; never load application .env."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, fields
from decimal import Decimal
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from src.database.config import ConfigurationError, validate_url
from src.database.runtime import run_async
from src.employee_source import EmployeeSourceService, EmployeeSourceSettings, EmployeeValidationError
from src.employee_source.connection import ReadOnlySource
from src.employee_source.contracts import (
    DATE_FIELDS, INTEGER_FIELDS, LIST_FIELDS, PROFILE_FIELDS, Degree, Family, WorkHistory,
    normalize_profile,
)
from src.employee_source.errors import EmployeeSourceError
from src.employee_source.repository import EmployeeRepository
from src.employee_source.selector import select_rows


def settings():
    return EmployeeSourceSettings(validate_url('postgresql+psycopg://reader:fictional@localhost/hrm_test'))


def row(**changes):
    result = {name: f' value:{name} ' for name in PROFILE_FIELDS}
    result.update({name: 100 + index for index, name in enumerate(INTEGER_FIELDS)})
    result.update({name: f'2020-01-{index + 1:02}' for index, name in enumerate(sorted(DATE_FIELDS))})
    result.update({name: [] for name in LIST_FIELDS})
    result.update(id=1, user_id=7, employee_id=42, account_id=9, active=True,
                  updated_at='2026-10-29 16:28:35.756954+07',
                  height=Decimal('170.123456789123456789'), weight=Decimal('60.25'))
    result.update(changes)
    return result


def child(model):
    result = {field.name: f' value:{field.name} ' for field in fields(model)}
    result['id'] = '001'
    for key in ('date_start', 'date_end', 'decision_date', 'birthday',
                'registration_date', 'from_date', 'to_date'):
        if key in result:
            result[key] = '2020-02-29'
    if 'is_dependent' in result:
        result['is_dependent'] = False
    return result


def test_all_64_fields_map_by_name_and_preserve_precision():
    source = row()
    profile = normalize_profile(source)
    assert len(PROFILE_FIELDS) == 64
    assert tuple(field.name for field in fields(profile)) == PROFILE_FIELDS
    for name, value in source.items():
        expected = value.strip() if isinstance(value, str) else value
        if name == 'updated_at':
            expected = '2026-10-29T16:28:35.756954+07:00'
        if name in LIST_FIELDS:
            expected = ()
        assert getattr(profile, name) == expected, name
    assert profile.height == Decimal('170.123456789123456789')
    assert 'value:' not in repr(profile)


def test_nested_contracts_nulls_unknown_codes_and_immutable_output():
    source = row(work_history=[child(WorkHistory)], degrees=json.dumps([child(Degree)]),
                 families=[child(Family)], trainings=[{'unknown': [{'amount': 1.25}]}],
                 notes='  ', active=None, employee_type='future_type', department_name=' A / B / C / D ')
    profile = normalize_profile(source)
    for name, model in [('work_history', WorkHistory), ('degrees', Degree), ('families', Family)]:
        item = getattr(profile, name)[0]
        for key, value in child(model).items():
            assert getattr(item, key) == (value.strip() if isinstance(value, str) else value), key
    assert profile.notes is None and profile.active is None
    assert profile.employee_type == 'future_type'
    assert profile.department_name == 'A / B / C / D'
    assert normalize_profile(row(work_history=None)).work_history is None
    with pytest.raises(FrozenInstanceError):
        profile.employee_name = 'changed'
    with pytest.raises(TypeError):
        profile.trainings[0]['unknown'] = ()
    source['work_history'][0]['job_title'] = 'changed'
    assert profile.work_history[0].job_title == 'value:job_title'
    assert profile.trainings[0]['unknown'][0]['amount'] == Decimal('1.25')
    assert normalize_profile(source) is not profile


@pytest.mark.parametrize('changes', [
    {'user_id': True}, {'employee_id': 0}, {'active': 1}, {'updated_at': '2020-01-01T00:00:00'},
    {'birthday': '2020-02-30'}, {'phone': 123}, {'height': float('nan')},
    {'updated_at': '2020-01-01T00:00:00+1460'},
    {'degrees': '{}'}, {'families': '[1]'}, {'work_history': [{}]}, {'trainings': '[NaN]'},
])
def test_invalid_data_is_sanitized(changes):
    with pytest.raises(EmployeeValidationError) as error:
        normalize_profile(row(**changes))
    assert 'value:' not in str(error.value)


def test_missing_projection_is_not_filled_with_null():
    source = row()
    del source['notes']
    with pytest.raises(EmployeeValidationError, match='invalid_projection'):
        normalize_profile(source)


def test_selector_matches_backend_priority_and_does_not_prefer_active():
    older = row(employee_id=99, active=True)
    newer = row(employee_id=1, active=False, updated_at='2026-10-29 09:28:35.756955Z')
    assert select_rows([older, newer], {7}) == [newer]
    assert select_rows([row(updated_at=None), older], {7}) == [older]
    sparse = row(employee_id=100, phone=' ')
    assert select_rows([sparse, older], {7}) == [older]
    assert select_rows([older, row(employee_id=100)], {7})[0]['employee_id'] == 100
    # Equal instants with different offsets still reach the ID tie-break.
    assert select_rows([older, row(employee_id=100, updated_at='2026-10-29T09:28:35.756954Z')], {7})[0]['employee_id'] == 100


def test_selector_conflict_before_trimming_and_unexpected_identity():
    with pytest.raises(EmployeeValidationError, match='conflicting_rows'):
        select_rows([row(notes='same'), row(notes=' same ')], {7})
    with pytest.raises(EmployeeValidationError, match='unexpected_identity'):
        select_rows([row(user_id=8)], {7})
    with pytest.raises(EmployeeValidationError, match='conflicting_rows'):
        select_rows([row(trainings=[{'value': True}]), row(trainings=[{'value': 1}])], {7})


def test_duplicate_projection_ignores_undeclared_source_columns():
    assert len(select_rows([row(extra='first'), row(extra='second')], {7})) == 1


def mocked_service(rows):
    service = EmployeeSourceService(settings())
    connection = MagicMock()
    connection.execute = AsyncMock()
    connection.execute.return_value = MagicMock()
    connection.execute.return_value.mappings.return_value = rows
    events = []
    @asynccontextmanager
    async def scope():
        events.append('begin')
        try:
            yield connection
        except BaseException:
            events.append('rollback')
            raise
        else:
            events.append('commit')
        finally:
            events.append('close')
    service._source._read_scope = scope
    return service, connection, events


def test_service_fixed_sql_mapping_missing_and_deduplication():
    service, connection, events = mocked_service([row(active=False)])
    result = run_async(service.find_many_by_user_ids([7, 7, 8]))
    assert set(result) == {7} and result[7].active is False
    assert events == ['begin', 'commit', 'close']
    query, parameters = connection.execute.call_args.args
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert 'SELECT *' not in sql and 'LIMIT' not in sql and 'ORDER BY' not in sql
    assert '"public"."employee_sso_store"' in sql
    assert 'WHERE "user_id" IN' in sql
    assert 'to_char("official_date",' in sql and '"updated_at"::text' in sql
    assert set(parameters['ids']) == {7, 8}
    assert all(f'"{name}"' in sql for name in PROFILE_FIELDS)
    with pytest.raises(TypeError):
        result[9] = result[7]
    empty, _, _ = mocked_service([])
    assert run_async(empty.find_by_user_id(7)) is None


@pytest.mark.parametrize('ids', [[True], [1.0], ['7'], [0], [-1], [2**53], [7]*201, '7'])
def test_bad_input_rejected_before_connection(ids):
    service = EmployeeSourceService()
    with patch.object(service._source, '_get_engine', side_effect=AssertionError('connection')):
        with pytest.raises(EmployeeValidationError):
            run_async(service.find_many_by_user_ids(ids))


def test_empty_batch_needs_no_settings_or_connection():
    service = EmployeeSourceService()
    assert dict(run_async(service.find_many_by_user_ids([]))) == {}
    assert service._source._engine is None


def test_batch_is_atomic_and_validation_runs_before_commit():
    service, _, events = mocked_service([row(), row(user_id=8, employee_id=43, birthday='invalid')])
    with pytest.raises(EmployeeValidationError):
        run_async(service.find_many_by_user_ids([7, 8]))
    assert events == ['begin', 'rollback', 'close']
    service, _, _ = mocked_service([row(), row(user_id=8)])
    with pytest.raises(EmployeeValidationError, match='invalid_identity'):
        run_async(service.find_many_by_user_ids([7, 8]))


def test_unknown_db_errors_and_parameters_never_escape():
    service, connection, events = mocked_service([])
    connection.execute.side_effect = OperationalError('private sql', {'secret': 'private'}, Exception('private'))
    with pytest.raises(EmployeeSourceError, match='source_unavailable') as error:
        run_async(service.find_by_user_id(7))
    assert 'private' not in str(error.value)
    assert events == ['begin', 'rollback', 'close']


def test_timeout_has_separate_error_and_no_automatic_retry():
    service, connection, _ = mocked_service([])
    class QueryTimeout(Exception):
        sqlstate = '57014'
    connection.execute.side_effect = OperationalError('private', {}, QueryTimeout('private'))
    with pytest.raises(EmployeeSourceError, match='source_timeout'):
        run_async(service.find_by_user_id(7))
    connection.execute.assert_awaited_once()


def test_service_cancellation_propagates_and_cleans_up():
    service, connection, events = mocked_service([])
    connection.execute.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        run_async(service.find_by_user_id(7))
    assert events == ['begin', 'rollback', 'close']


def test_normal_transaction_commits_and_releases_scope():
    source = ReadOnlySource(settings())
    connection = AsyncMock()
    connection.scalar.return_value = 'on'
    transaction = AsyncMock()
    connection.begin = MagicMock(return_value=transaction)
    acquired = AsyncMock()
    acquired.__aenter__.return_value = connection
    source._engine = MagicMock()
    source._engine.connect.return_value = acquired
    async def exercise():
        async with source._read_scope():
            pass
    run_async(exercise())
    transaction.__aexit__.assert_awaited_once_with(None, None, None)
    acquired.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.parametrize('cancel', [False, True])
def test_real_scope_sets_readonly_and_closes_on_error_or_cancel(cancel):
    source = ReadOnlySource(settings())
    connection = AsyncMock()
    connection.scalar.return_value = 'on'
    transaction = AsyncMock()
    connection.begin = MagicMock(return_value=transaction)
    acquired = AsyncMock()
    acquired.__aenter__.return_value = connection
    engine = MagicMock()
    engine.connect.return_value = acquired
    source._engine = engine
    async def exercise():
        async with source._read_scope():
            raise asyncio.CancelledError() if cancel else ValueError('test')
    with pytest.raises(asyncio.CancelledError if cancel else ValueError):
        run_async(exercise())
    assert [str(call.args[0]) for call in connection.execute.call_args_list] == [
        'SET TRANSACTION READ ONLY', "SET LOCAL statement_timeout = '10s'"]
    assert transaction.__aexit__.await_count == 1
    assert acquired.__aexit__.await_count == 1


def test_readonly_verification_fail_closes_transaction():
    source = ReadOnlySource(settings())
    connection = AsyncMock()
    connection.scalar.return_value = 'off'
    connection.begin = MagicMock(return_value=AsyncMock())
    acquired = AsyncMock()
    acquired.__aenter__.return_value = connection
    source._engine = MagicMock()
    source._engine.connect.return_value = acquired
    async def exercise():
        async with source._read_scope():
            pytest.fail('Readonly was not enforced')
    with pytest.raises(EmployeeSourceError, match='readonly_required'):
        run_async(exercise())
    acquired.__aexit__.assert_awaited_once()


def test_lazy_independent_engine_and_configuration(monkeypatch):
    monkeypatch.delenv('HRM_DATABASE_URL', raising=False)
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://writer:private@localhost/catalog')
    source = ReadOnlySource()
    assert source._engine is None
    with pytest.raises(ConfigurationError, match='HRM_DATABASE_URL'):
        source._get_engine()
    with patch('src.employee_source.connection.create_async_engine') as factory:
        source = ReadOnlySource(settings())
        factory.return_value.dispose = AsyncMock()
        engine = source._get_engine()
        assert source._get_engine() is engine
        factory.assert_called_once()
        kwargs = factory.call_args.kwargs
        assert kwargs['pool_size'] == 2 and kwargs['max_overflow'] == 0
        assert 'default_transaction_read_only=on' in kwargs['connect_args']['options']
        assert kwargs['isolation_level'] == 'REPEATABLE READ'
        assert not kwargs['echo'] and kwargs['hide_parameters']
        run_async(source.close())
        engine.dispose.assert_awaited_once()
        assert source._engine is None
        assert 'fictional' not in repr(settings())


@pytest.mark.parametrize('schema', ['public; DROP TABLE x', 'x.y', '"public"', '', 'a'*64])
def test_schema_is_validated(schema):
    with pytest.raises(ConfigurationError):
        EmployeeSourceSettings(settings().url, schema)
    with pytest.raises(ConfigurationError):
        EmployeeRepository(schema)


def test_startup_option_override_rejected():
    with pytest.raises(ConfigurationError):
        EmployeeSourceSettings(validate_url('postgresql+psycopg://u:p@localhost/hrm?options=private'))


def test_import_has_no_dotenv_or_connection_side_effects():
    import subprocess
    import sys
    code = '''
from unittest.mock import patch
with patch('dotenv.load_dotenv', side_effect=AssertionError('dotenv')), \\
     patch('sqlalchemy.ext.asyncio.create_async_engine', side_effect=AssertionError('engine')):
    import src.employee_source
    import src.cli.check_employee_source
    service = src.employee_source.EmployeeSourceService()
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not result.stdout


def test_check_metadata_and_projection_only():
    async def exercise():
        repository = EmployeeRepository('public')
        connection = AsyncMock()
        metadata = MagicMock()
        types = {name: 'text' for name in PROFILE_FIELDS}
        types.update({name: 'int4' for name in INTEGER_FIELDS})
        types.update({name: 'date' for name in DATE_FIELDS})
        types.update(updated_at='timestamptz', active='bool', height='numeric', weight='numeric')
        metadata.tuples.return_value = list(types.items())
        connection.execute.return_value = metadata
        connection.scalar.return_value = False
        assert await repository._check(connection) == {'status': 'ok', 'readonly': True, 'projection_columns': 64}
        assert 'WHERE false' in str(connection.execute.call_args.args[0])
        connection.scalar.return_value = True
        with pytest.raises(EmployeeSourceError, match='unsafe_database_role'):
            await repository._check(connection)
        types.pop('notes')
        metadata.tuples.return_value = list(types.items())
        with pytest.raises(EmployeeSourceError, match='schema_incompatible'):
            await repository._check(connection)
    run_async(exercise())


def test_cli_cleans_up_and_prints_only_safe_status(monkeypatch, capsys):
    from src.cli import check_employee_source as cli
    monkeypatch.setattr(cli, 'load_dotenv', MagicMock())
    service = MagicMock()
    service.check_source = AsyncMock(return_value={})
    service.close = AsyncMock()
    monkeypatch.setattr(cli, 'EmployeeSourceService', lambda: service)
    assert run_async(cli.main()) == 0
    service.close.assert_awaited_once()
    assert 'Employee source OK' in capsys.readouterr().out
    service.check_source.side_effect = RuntimeError('private credentials')
    assert run_async(cli.main()) == 1
    captured = capsys.readouterr()
    assert 'private' not in captured.out + captured.err
    assert service.close.await_count == 2
    monkeypatch.setattr(cli, 'load_dotenv', MagicMock(side_effect=OSError('private path')))
    assert run_async(cli.main()) == 1
    captured = capsys.readouterr()
    assert 'private' not in captured.out + captured.err
    assert service.close.await_count == 3
