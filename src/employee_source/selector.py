"""Backend-compatible representative selection, before presentation normalization."""
from datetime import datetime, timezone
from decimal import Decimal

from .contracts import PROFILE_FIELDS, identity, source_timestamp
from .errors import EmployeeValidationError

TEXT_FIELDS = ('employee_name', 'employee_code', 'work_email', 'phone',
               'avatar_url', 'job_title', 'department_name')


def _strict_equal(left, right):
    # JS isDeepStrictEqual distinguishes boolean from numeric JSON values.
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float, Decimal)) and isinstance(right, (int, float, Decimal)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_strict_equal(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(_strict_equal(a, b) for a, b in zip(left, right))
    return left == right


def score(row):
    return sum(isinstance(row.get(key), str) and bool(row[key].strip()) for key in TEXT_FIELDS) + (
        type(row.get('active')) is bool or row.get('active') in ('true', 'false'))


def select_rows(rows, requested):
    groups = {}
    for row in rows:
        user_id = identity(row.get('user_id'))
        if user_id not in requested:
            raise EmployeeValidationError('unexpected_identity')
        groups.setdefault(user_id, []).append(row)
    selected = []
    for group in groups.values():
        if len(group) == 1:
            selected.append(group[0])
            continue
        def rank(row):
            timestamp = source_timestamp(row.get('updated_at'))
            return (timestamp is not None, timestamp or datetime.min.replace(tzinfo=timezone.utc),
                    score(row), identity(row.get('employee_id')))
        ranked = sorted(((rank(row), row) for row in group), key=lambda item: item[0], reverse=True)
        best_rank, best = ranked[0]
        for other_rank, other in ranked[1:]:
            if other_rank == best_rank and any(not _strict_equal(other.get(key), best.get(key)) for key in PROFILE_FIELDS):
                raise EmployeeValidationError('conflicting_rows')
        selected.append(best)
    return selected
