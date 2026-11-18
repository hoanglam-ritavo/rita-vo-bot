"""Pure source validation; preserves nulls, precision and unknown enum strings."""
from dataclasses import fields
from datetime import date, datetime
from decimal import Decimal
import json
import math
import re
from types import MappingProxyType
from typing import Mapping

from .contracts import EmployeeProfile, ManagerSummary
from .fields import MAX_ID, PROFILE_FIELDS, DATE_FIELDS, INTEGER_FIELDS, LIST_FIELDS
from .errors import EmployeeValidationError


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


def text_value(value):
    if value is None:
        return None
    if not isinstance(value, str):
        invalid()
    return None if value.strip() in ('', 'NULL') else value.strip()


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


def normalize_values(row: Mapping, names) -> dict:
    if any(name not in row for name in names):
        invalid('invalid_projection')
    result = {}
    for name in names:
        value = row[name]
        if name == 'employee_id' or name == 'user_id' and value is not None:
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
    return result


def normalize_profile(row: Mapping) -> EmployeeProfile:
    return EmployeeProfile(**normalize_values(row, PROFILE_FIELDS))


def normalize_manager(row: Mapping) -> ManagerSummary:
    """Validate only the declared work summary, not unrelated private fields."""
    result = {}
    for item in fields(ManagerSummary):
        name = item.name
        if name not in row:
            invalid('invalid_projection')
        value = row[name]
        if name == 'employee_id':
            value = identity(value)
        elif name == 'user_id':
            value = None if value is None else identity(value)
        elif name == 'active':
            value = boolean_value(value)
        elif name == 'updated_at':
            parsed = source_timestamp(value)
            value = parsed.isoformat(timespec='microseconds') if parsed else None
        else:
            value = text_value(value)
        result[name] = value
    return ManagerSummary(**result)
