"""Immutable personnel models and internal reader interface; no I/O or authorization."""
from dataclasses import dataclass
from decimal import Decimal
from collections.abc import Mapping
from typing import Literal, Protocol


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
    user_id: int | None
    account_id: int | None
    private_email: str | None
    contract_kind: str | None
    private_state_id: int | None
    private_state_name: str | None
    temporary_state_id: int | None
    temporary_state_name: str | None


@dataclass(frozen=True, repr=False)
class ManagerSummary:
    employee_id: int
    user_id: int | None
    employee_code: str | None
    employee_name: str | None
    job_role_name: str | None
    job_title: str | None
    department_name: str | None
    work_email: str | None
    phone: str | None
    active: bool | None
    updated_at: str | None


ManagerStatus = Literal['found', 'employee_not_found', 'missing_link', 'manager_not_found',
                        'ambiguous', 'conflicting_link', 'self_reference']


@dataclass(frozen=True, repr=False)
class ManagerResult:
    status: ManagerStatus
    manager: ManagerSummary | None = None

    def __post_init__(self):
        if self.status not in ('found', 'employee_not_found', 'missing_link', 'manager_not_found',
                               'ambiguous', 'conflicting_link', 'self_reference') or (
                (self.status == 'found') != (self.manager is not None)):
            raise ValueError('invalid_manager_result')


class EmployeeStoreReader(Protocol):
    """Internal access only; callers establish authorization before using this interface."""

    async def find_by_user_id(self, user_id: int) -> EmployeeProfile | None: ...
    async def find_many_by_user_ids(self, user_ids: list[int] | tuple[int, ...]) -> Mapping[int, EmployeeProfile]: ...
    async def find_by_employee_id(self, employee_id: int) -> EmployeeProfile | None: ...
    async def find_many_by_employee_ids(self, employee_ids: list[int] | tuple[int, ...]) -> Mapping[int, EmployeeProfile]: ...
    async def find_manager_by_user_id(self, user_id: int) -> ManagerResult: ...
    async def find_managers_by_user_ids(self, user_ids: list[int] | tuple[int, ...]) -> Mapping[int, ManagerResult]: ...
    async def check_source(self) -> dict: ...
    async def close(self) -> None: ...
    async def read_context(self, user_id: int, fields: tuple[str, ...], *, manager: bool = False) -> 'EmployeeContext | None': ...


@dataclass(frozen=True, repr=False)
class EmployeeContext:
    values: Mapping[str, object]
    manager: ManagerResult | None
    observed_at: str
    updated_at: str | None


