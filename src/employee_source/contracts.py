"""Immutable source projection. This is not an ORM model or an authorization DTO."""
from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal
import json
import math
import re
from types import MappingProxyType
from typing import Mapping

from .errors import EmployeeValidationError

MAX_ID = 2**53 - 1


def invalid(code='invalid_data'):
    raise EmployeeValidationError(code)


def identity(value):
    if isinstance(value, str) and re.fullmatch(r'[1-9]\d*', value):
        value = int(value)
    if type(value) is not int or not 0 < value <= MAX_ID:
        invalid('invalid_identity')
    return value


def source_timestamp(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and re.fullmatch(
        r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}(?::?\d{2})?)', value
    ):
        zone = re.search(r'(Z|[+-]\d{2}(?::?\d{2})?)$', value).group(0)
        if zone != 'Z':
            digits = zone[1:].replace(':', '').ljust(4, '0')
            if int(digits[:2]) > 15 or int(digits[2:]) > 59:
                invalid('invalid_timestamp')
        try:
            result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            invalid('invalid_timestamp')
    else:
        invalid('invalid_timestamp')
    if result.tzinfo is None or result.utcoffset() is None or abs(result.utcoffset().total_seconds()) >= 16 * 3600:
        invalid('invalid_timestamp')
    return result


@dataclass(frozen=True, repr=False)
class WorkHistory:
    id: str | int
    date_start: str | None
    date_end: str | None
    job_title: str | None
    transfer_type: str | None
    work_location: str | None
    decision_number: str | None
    decision_date: str | None
    decision_place: str | None
    department_name: str | None
    description: str | None
    job_role_name: str | None
    transfer_type_display: str | None


@dataclass(frozen=True, repr=False)
class Degree:
    id: str | int
    degree_type: str | None
    major: str | None
    school: str | None
    level_display: str | None
    grade_display: str | None
    grade: str | None
    graduation_year: str | None
    level: str | None


@dataclass(frozen=True, repr=False)
class Family:
    id: str | int
    name: str | None
    birthday: str | None
    gender: str | None
    gender_display: str | None
    relation_type: str | None
    relation_type_display: str | None
    is_dependent: bool | None
    job: str | None
    phone: str | None
    tax_id: str | None
    registration_date: str | None
    from_date: str | None
    to_date: str | None


@dataclass(frozen=True, repr=False)
class EmployeeProfile:
    id: int
    employee_id: int
    employee_code: str | None
    employee_name: str | None
    job_role_name: str | None
    job_title: str | None
    department_name: str | None
    avatar_url: str | None
    work_email: str | None
    phone: str | None
    active: bool | None
    status_display: str | None
    updated_at: str | None
    old_employee_code: str | None
    probation_date: str | None
    probation_end_date: str | None
    official_date: str | None
    resignation_date: str | None
    employee_type: str | None
    employee_group: str | None
    department_address: str | None
    birthday: str | None
    place_of_birth: str | None
    hometown: str | None
    ethnicity: str | None
    religion: str | None
    nationality: str | None
    marital: str | None
    social_insurance_number: str | None
    social_insurance_date: str | None
    health_insurance_number: str | None
    health_clinic: str | None
    blood_type: str | None
    height: Decimal | None
    weight: Decimal | None
    permanent_address: str | None
    temporary_address: str | None
    emergency_contact: str | None
    emergency_address: str | None
    emergency_phone: str | None
    notes: str | None
    tax_id: str | None
    identification_id: str | None
    id_issue_date: str | None
    id_issue_place: str | None
    bank_account_number: str | None
    bank_name: str | None
    bank_branch: str | None
    bank_account_name: str | None
    work_history: tuple[WorkHistory, ...] | None
    degrees: tuple[Degree, ...] | None
    trainings: tuple[object, ...] | None
    families: tuple[Family, ...] | None
    manager_id: int | None
    manager_code: str | None
    manager_name: str | None
    user_id: int
    account_id: int | None
    private_email: str | None
    contract_kind: str | None
    private_state_id: int | None
    private_state_name: str | None
    temporary_state_id: int | None
    temporary_state_name: str | None


PROFILE_FIELDS = tuple(item.name for item in fields(EmployeeProfile))
DATE_FIELDS = frozenset(('probation_date', 'probation_end_date', 'official_date',
                         'resignation_date', 'birthday', 'social_insurance_date', 'id_issue_date'))
INTEGER_FIELDS = frozenset(('id', 'employee_id', 'user_id', 'account_id', 'manager_id',
                            'private_state_id', 'temporary_state_id'))
LIST_FIELDS = {'work_history': WorkHistory, 'degrees': Degree, 'families': Family, 'trainings': None}


def text_value(value):
    if value is None:
        return None
    if not isinstance(value, str):
        invalid()
    return value.strip() or None


def date_value(value):
    if value is None:
        return None
    if type(value) is date:
        return value.isoformat()
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        invalid()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        invalid()


def boolean_value(value):
    if value is None or type(value) is bool:
        return value
    if value in ('true', 'false'):
        return value == 'true'
    invalid()


def freeze_json(value):
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return MappingProxyType({key: freeze_json(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(freeze_json(child) for child in value)
    if value is None or type(value) in (str, bool, int):
        return value
    if isinstance(value, Decimal) and value.is_finite():
        return value
    if type(value) is float and math.isfinite(value):
        return Decimal(str(value))
    invalid()


def list_value(value, model):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value, parse_float=Decimal)
        except (ValueError, TypeError):
            invalid('invalid_list')
    if not isinstance(value, list):
        invalid('invalid_list')
    if model is None:
        return tuple(freeze_json(item) for item in value)
    result = []
    for item in value:
        if not isinstance(item, dict):
            invalid('invalid_list')
        converted = {}
        for field in fields(model):
            if field.name not in item:
                invalid('invalid_list')
            raw = item[field.name]
            if field.name == 'id':
                if type(raw) not in (str, int):
                    invalid('invalid_list')
                converted[field.name] = raw
            elif field.name == 'is_dependent':
                converted[field.name] = boolean_value(raw)
            elif field.name in ('birthday', 'date_start', 'date_end', 'decision_date',
                                'registration_date', 'from_date', 'to_date'):
                converted[field.name] = date_value(raw)
            else:
                converted[field.name] = text_value(raw)
        result.append(model(**converted))
    return tuple(result)


def normalize_profile(row: Mapping) -> EmployeeProfile:
    if any(name not in row for name in PROFILE_FIELDS):
        invalid('invalid_projection')
    result = {}
    for name in PROFILE_FIELDS:
        value = row[name]
        if name in ('user_id', 'employee_id'):
            result[name] = identity(value)
        elif name in INTEGER_FIELDS:
            if value is None and name != 'id':
                result[name] = None
            elif name in ('id', 'manager_id', 'private_state_id', 'temporary_state_id'):
                if type(value) is not int or abs(value) > MAX_ID:
                    invalid()
                result[name] = value
            else:
                result[name] = identity(value)
        elif name in DATE_FIELDS:
            result[name] = date_value(value)
        elif name == 'updated_at':
            parsed = source_timestamp(value)
            result[name] = parsed.isoformat(timespec='microseconds') if parsed else None
        elif name == 'active':
            result[name] = boolean_value(value)
        elif name in ('height', 'weight'):
            if value is None:
                result[name] = None
            elif type(value) in (int, float, Decimal):
                number = Decimal(str(value))
                if not number.is_finite():
                    invalid()
                result[name] = number
            else:
                invalid()
        elif name in LIST_FIELDS:
            result[name] = list_value(value, LIST_FIELDS[name])
        else:
            result[name] = text_value(value)
    return EmployeeProfile(**result)
