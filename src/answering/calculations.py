"""Exact bounded arithmetic, independent of language matching and employee access."""
from calendar import monthrange
from datetime import date
from decimal import Decimal


def calculate(operation, values, *, as_of=None, thresholds=()):
    if operation in ('interval_days', 'completed_months', 'age_years'):
        start, end = date.fromisoformat(values[0]), date.fromisoformat(as_of)
        if end < start:
            raise ValueError('invalid_date_interval')
        if operation == 'interval_days':
            return str((end - start).days), 'day'
        if operation == 'age_years':
            return str(end.year - start.year - ((end.month, end.day) < (start.month, start.day))), 'year'
        months = (end.year-start.year)*12 + end.month-start.month
        months -= end.day < min(start.day, monthrange(end.year, end.month)[1])
        return str(months), 'month'
    numbers = [Decimal(str(value)) for value in values]
    if not numbers or any(not number.is_finite() for number in numbers):
        raise ValueError('invalid_calculation_input')
    if operation == 'multiply':
        result = Decimal(1)
        for number in numbers:
            result *= number
    elif operation == 'add':
        result = sum(numbers)
    elif operation == 'count':
        result = Decimal(len(numbers))
    elif operation == 'tier':
        if not thresholds or any(Decimal(str(a)) >= Decimal(str(b)) for a, b in zip(thresholds, thresholds[1:])):
            raise ValueError('invalid_tier_definition')
        result = Decimal(sum(numbers[0] >= Decimal(str(t)) for t in thresholds))
    else:
        raise ValueError('unsupported_calculation')
    return format(result, 'f'), 'number'
