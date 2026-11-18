"""Employee identity, manager resolution and lifecycle boundaries with fictional data."""
import asyncio
from dataclasses import fields, FrozenInstanceError
from unittest.mock import MagicMock, AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from src.database.runtime import run_async
from src.type_b.employee_store import EmployeeSourceError, EmployeeValidationError
from src.type_b.employee_store.contracts import EmployeeProfile, ManagerSummary, ManagerResult
from src.type_b.employee_store.fields import FIELD_REGISTRY, WORK_FIELDS
from src.type_b.employee_store.normalization import normalize_profile
from src.tests.test_employee_source import row, mocked_service


def store(*rowsets):
    service, connection, events = mocked_service([])
    results = []
    for rows in rowsets:
        result = MagicMock()
        result.mappings.return_value = rows
        results.append(result)
    connection.execute.side_effect = results
    return service, connection, events


def manager(**changes):
    return row(employee_id=90, user_id=None, employee_code='M90',
               employee_name='Fictional Manager', work_email='manager@example.test', **changes)


def test_registry_and_internal_nullable_user_id():
    assert tuple(FIELD_REGISTRY) == tuple(f.name for f in fields(EmployeeProfile))
    assert len(FIELD_REGISTRY) == 64
    assert FIELD_REGISTRY['height'].unit == 'cm'
    assert FIELD_REGISTRY['weight'].unit == 'kg'
    assert FIELD_REGISTRY['user_id'].nullable
    assert not FIELD_REGISTRY['employee_id'].nullable
    assert normalize_profile(row(user_id=None)).user_id is None
    assert normalize_profile(row(notes=' NULL ', work_email='   ')).notes is None
    with pytest.raises(TypeError):
        FIELD_REGISTRY['extra'] = None


def test_employee_id_lookup_preserves_exact_entity_and_unlinked_profile():
    service, connection, events = store([row(user_id=None), row(user_id=None, phone='changed',
        updated_at='2026-10-30T00:00:00Z')])
    profile = run_async(service.find_by_employee_id(42))
    assert profile.user_id is None and profile.phone == 'changed'
    sql = str(connection.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert 'WHERE "employee_id" IN' in sql and 'LIMIT' not in sql
    assert connection.execute.call_args.args[1] == {'ids': (42,)}
    assert events == ['begin', 'commit', 'close']


def test_employee_batch_missing_dedup_and_atomic_validation():
    service, connection, _ = store([row(), row(user_id=7, employee_id=43)])
    profiles = run_async(service.find_many_by_employee_ids([42, 42, 43, 99]))
    assert set(profiles) == {42, 43}
    assert len(connection.execute.call_args.args[1]['ids']) == 3
    with pytest.raises(TypeError):
        profiles[99] = profiles[42]
    service, _, events = store([row(), row(employee_id=43, families='{}')])
    with pytest.raises(EmployeeValidationError):
        run_async(service.find_many_by_employee_ids([42, 43]))
    assert events == ['begin', 'rollback', 'close']


@pytest.mark.parametrize('method,values', [
    ('find_many_by_employee_ids', [True]), ('find_many_by_employee_ids', ['42']),
    ('find_many_by_employee_ids', [1.5]), ('find_many_by_employee_ids', [0]),
    ('find_managers_by_user_ids', [2**53]), ('find_managers_by_user_ids', [7]*201),
    ('find_managers_by_user_ids', '7'),
])
def test_new_lookups_reject_input_before_connection(method, values):
    service, connection, _ = store()
    with pytest.raises(EmployeeValidationError):
        run_async(getattr(service, method)(values))
    connection.execute.assert_not_awaited()


def test_employee_lookup_missing_unexpected_and_conflict():
    service, _, _ = store([])
    assert run_async(service.find_by_employee_id(99)) is None
    service, _, _ = store([row(employee_id=43)])
    with pytest.raises(EmployeeValidationError, match='unexpected_identity'):
        run_async(service.find_by_employee_id(42))
    service, _, _ = store([row(), row(employee_name='conflict')])
    with pytest.raises(EmployeeValidationError, match='conflicting_rows'):
        run_async(service.find_by_employee_id(42))


def test_manager_by_id_has_only_work_fields_and_keeps_inactive_unlinked():
    service, connection, events = store([row(manager_id=90, manager_code=' M90 ')],
                                       [manager(active=False, families='malformed private list')])
    result = run_async(service.find_manager_by_user_id(7))
    assert result.status == 'found'
    assert result.manager.employee_id == 90
    assert result.manager.work_email == 'manager@example.test'
    assert result.manager.user_id is None and result.manager.active is False
    assert not hasattr(result.manager, 'families') and not hasattr(result.manager, 'account_id')
    assert 'manager@example.test' not in repr(result.manager) + repr(result)
    with pytest.raises(FrozenInstanceError):
        result.manager.phone = 'changed'
    assert events == ['begin', 'commit', 'close']
    assert connection.execute.await_count == 2
    for call in connection.execute.call_args_list:
        sql = str(call.args[0])
        assert all(f'"{field}"' in sql for field in WORK_FIELDS)
        assert '"families"' not in sql and '"bank_account_number"' not in sql


@pytest.mark.parametrize('subject,targets,expected', [
    (row(manager_id=None, manager_code=None), None, 'missing_link'),
    (row(manager_id=None, manager_code=' NULL '), None, 'missing_link'),
    (row(manager_id=90, manager_code='M90'), [], 'manager_not_found'),
    (row(manager_id=90, manager_code='wrong'), [manager()], 'conflicting_link'),
    (row(manager_id=None, manager_code='M90'), [manager()], 'found'),
    (row(manager_id=None, manager_code='M90'), [], 'manager_not_found'),
    (row(manager_id=None, manager_code='m90'), [manager()], 'manager_not_found'),
    (row(manager_id=None, manager_code='M90'), [manager(),
        row(employee_id=91, user_id=None, employee_code='M90')], 'ambiguous'),
    (row(manager_id=42, manager_code=None), [row()], 'self_reference'),
    (row(manager_id=None, manager_code='SELF'), [row(employee_code='SELF')], 'self_reference'),
    (row(manager_id=90, manager_code=None), [row(employee_id=90)], 'self_reference'),
])
def test_manager_relation_statuses(subject, targets, expected):
    rowsets = [[subject]] + ([] if targets is None else [targets])
    service, connection, _ = store(*rowsets)
    result = run_async(service.find_manager_by_user_id(7))
    assert result.status == expected
    assert (result.manager is not None) == (expected == 'found')
    assert connection.execute.await_count == len(rowsets)


def test_missing_manager_id_target_does_not_fall_back_to_code():
    service, connection, _ = store([row(manager_id=90, manager_code='M90')], [])
    assert run_async(service.find_manager_by_user_id(7)).status == 'manager_not_found'
    assert connection.execute.await_count == 2
    assert 'btrim' not in str(connection.execute.call_args.args[0])


def test_code_lookup_fetches_full_groups_and_rechecks_representative_code():
    service, connection, _ = store([row(manager_id=None, manager_code=" M90' ")], [
        row(employee_id=90, user_id=None, employee_code="M90'"),
        row(employee_id=90, user_id=None, employee_code='NEW', updated_at='2026-11-01T00:00:00Z')])
    assert run_async(service.find_manager_by_user_id(7)).status == 'manager_not_found'
    statement, parameters = connection.execute.call_args.args
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert 'SELECT "employee_id" FROM' in sql and 'btrim' in sql
    assert 'LIMIT' not in sql and "M90'" not in sql
    assert parameters['codes'] == ("M90'",)
    assert parameters['whitespace'] == ''.join(chr(i) for i in range(0x3100) if chr(i).isspace())


def test_manager_batch_uses_one_scope_and_at_most_three_queries():
    subjects = [row(user_id=i, employee_id=100+i, manager_id=90 if i%2 else None,
                    manager_code='M90') for i in range(1, 201)]
    service, connection, events = store(subjects, [manager()], [manager()])
    results = run_async(service.find_managers_by_user_ids(list(range(1, 201))))
    assert len(results) == 200 and all(r.status == 'found' for r in results.values())
    assert connection.execute.await_count == 3
    assert events == ['begin', 'commit', 'close']
    with pytest.raises(TypeError):
        results[1] = None


def test_manager_batch_absent_subject_and_newer_inactive_subject():
    service, connection, _ = store([row(manager_id=90, manager_code='M90'),
        row(manager_id=None, manager_code=None, active=False, updated_at='2026-11-01T00:00:00Z')])
    results = run_async(service.find_managers_by_user_ids([7, 8, 7]))
    assert results[7].status == 'missing_link'
    assert results[8].status == 'employee_not_found'
    connection.execute.assert_awaited_once()


@pytest.mark.parametrize('rowsets', [
    [[row(manager_id=-1)]],
    [[row(manager_id=90)], [row(employee_id=91)]],
    [[row(manager_id=90)], [manager(), row(employee_id=90, user_id=None, employee_name='conflict')]],
    [[row(manager_id=90)], [row(employee_id=90, user_id=None, work_email=123)]],
])
def test_bad_manager_data_is_error_not_absence(rowsets):
    service, _, events = store(*rowsets)
    with pytest.raises(EmployeeValidationError):
        run_async(service.find_manager_by_user_id(7))
    assert events == ['begin', 'rollback', 'close']


def test_empty_new_batches_are_lazy_and_closed_service_cannot_reopen():
    async def exercise():
        service, connection, _ = store()
        assert dict(await service.find_many_by_employee_ids([])) == {}
        assert dict(await service.find_managers_by_user_ids([])) == {}
        assert service._source._engine is None
        await service.close()
        await service.close()
        for read in (service.find_many_by_employee_ids([]), service.find_managers_by_user_ids([]),
                     service.find_by_user_id(7), service.check_source()):
            with pytest.raises(EmployeeSourceError, match='source_closed'):
                await read
        connection.execute.assert_not_awaited()
    run_async(exercise())


def test_close_drains_reads_is_idempotent_and_survives_cancelled_closer():
    async def exercise():
        service, connection, events = store()
        entered, release = asyncio.Event(), asyncio.Event()
        async def execute(*args):
            entered.set()
            await release.wait()
            result = MagicMock()
            result.mappings.return_value = [row()]
            return result
        connection.execute.side_effect = execute
        service._source.close = AsyncMock()
        reading = asyncio.create_task(service.find_by_user_id(7))
        await entered.wait()
        closing = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        with pytest.raises(EmployeeSourceError, match='source_closed'):
            await service.find_by_employee_id(42)
        service._source.close.assert_not_awaited()
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        release.set()
        assert (await reading).employee_id == 42
        await asyncio.gather(service.close(), service.close())
        service._source.close.assert_awaited_once()
        assert events == ['begin', 'commit', 'close']
    run_async(exercise())


def test_cancel_manager_read_rolls_back_before_disposal():
    async def exercise():
        service, connection, events = store([row(manager_id=90)])
        # Cancel during the second query, after subject selection.
        calls = 0
        async def execute(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise asyncio.CancelledError()
            result = MagicMock()
            result.mappings.return_value = [row(manager_id=90)]
            return result
        connection.execute.side_effect = execute
        with pytest.raises(asyncio.CancelledError):
            await service.find_manager_by_user_id(7)
        assert events == ['begin', 'rollback', 'close']
        await service.close()
    run_async(exercise())


def test_result_invariants_and_no_old_package_imports():
    with pytest.raises(ValueError, match='invalid_manager_result'):
        ManagerResult('found')
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert not list((root/'employee_source').glob('*.py'))
    assert {f.name for f in fields(ManagerSummary)}.isdisjoint(
        {'notes', 'families', 'private_email', 'bank_account_number', 'birthday'})


def test_context_projection_and_manager_share_one_transaction():
    service, connection, events = store([row(manager_id=90, manager_code='M90')], [manager()])
    context = run_async(service.read_context(7, ('official_date',), manager=True))
    assert set(context.values) == {'official_date'} and context.manager.status == 'found'
    assert context.manager.manager.work_email == 'manager@example.test'
    assert events == ['begin', 'commit', 'close']
    for call in connection.execute.await_args_list:
        sql = str(call.args[0])
        assert 'bank_account_number' not in sql and 'families' not in sql and 'notes' not in sql
    with pytest.raises(TypeError):context.values['extra'] = 'no'


def test_context_validates_only_selected_fields_and_preserves_ranking():
    service, connection, _ = store([row(notes=123, official_date='2020-01-01')])
    context = run_async(service.read_context(7, ('official_date',)))
    assert context.values['official_date'] == '2020-01-01'
    sql = str(connection.execute.call_args.args[0])
    assert 'official_date' in sql and 'active' in sql and 'notes' not in sql
    with pytest.raises(EmployeeValidationError):
        run_async(service.read_context(7, ('account_id',)))
