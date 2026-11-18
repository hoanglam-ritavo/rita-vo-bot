"""Single projection/type registry. Meanings and upstream unknowns: docs/employee-source.md."""
from dataclasses import dataclass, fields
from types import MappingProxyType

from .contracts import EmployeeProfile, WorkHistory, Degree, Family

MAX_ID = 2**53 - 1
MAX_BATCH = 200
RELATION = 'employee_sso_store'
DATE_FIELDS = frozenset(('probation_date', 'probation_end_date', 'official_date',
                         'resignation_date', 'birthday', 'social_insurance_date', 'id_issue_date'))
INTEGER_FIELDS = frozenset(('id', 'employee_id', 'user_id', 'account_id', 'manager_id',
                            'private_state_id', 'temporary_state_id'))
LIST_FIELDS = MappingProxyType({'work_history': WorkHistory, 'degrees': Degree,
                               'families': Family, 'trainings': None})
RANK_TEXT_FIELDS = ('employee_name', 'employee_code', 'work_email', 'phone',
                    'avatar_url', 'job_title', 'department_name')


@dataclass(frozen=True)
class SourceField:
    name: str
    category: str
    nullable: bool
    postgres_types: frozenset[str]
    unit: str | None = None


def _field(name):
    category, types = (
        ('integer', {'int2', 'int4', 'int8'}) if name in INTEGER_FIELDS else
        ('date', {'date', 'timestamp', 'timestamptz'}) if name in DATE_FIELDS else
        ('timestamp', {'timestamptz'}) if name == 'updated_at' else
        ('boolean', {'bool'}) if name == 'active' else
        ('decimal', {'numeric', 'float4', 'float8', 'int2', 'int4', 'int8'}) if name in ('height', 'weight') else
        ('collection', {'json', 'jsonb', 'text', 'varchar'}) if name in LIST_FIELDS else
        ('text', {'text', 'varchar', 'bpchar'}))
    return SourceField(name, category, name not in ('id', 'employee_id'), frozenset(types),
                       {'height': 'cm', 'weight': 'kg'}.get(name))


FIELD_REGISTRY = MappingProxyType({item.name: _field(item.name) for item in fields(EmployeeProfile)})
PROFILE_FIELDS = tuple(FIELD_REGISTRY)
# Rank fields are always included; manager reads never fetch financial/family/health data.
WORK_FIELDS = tuple(dict.fromkeys(('employee_id', 'user_id', *RANK_TEXT_FIELDS,
                                  'job_role_name', 'active', 'updated_at', 'manager_id', 'manager_code')))
