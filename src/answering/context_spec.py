"""Authored contextual requirements. These never grant application permissions."""
import string
from typing import Literal
from pydantic import Field, model_validator
from .contracts import Strict

GROUPS = {
    'identity': ('employee_name',),
    'work': ('employee_name', 'employee_code', 'job_title', 'job_role_name', 'department_name', 'work_email', 'phone'),
    'employment': ('probation_date', 'probation_end_date', 'official_date', 'resignation_date', 'employee_type', 'contract_kind'),
    'family': ('families',),
    'insurance': ('social_insurance_number', 'social_insurance_date', 'health_insurance_number', 'health_clinic'),
    'identity_documents': ('identification_id', 'id_issue_date', 'id_issue_place', 'tax_id'),
    'bank': ('bank_account_number', 'bank_account_name', 'bank_name', 'bank_branch'),
}


class ContextSpec(Strict):
    mode: Literal['none', 'fill', 'branch', 'calculation'] = 'none'
    groups: list[Literal['identity', 'work', 'employment', 'family', 'insurance', 'identity_documents', 'bank']] = Field(default_factory=list)
    manager: bool = False
    facts: list[str] = Field(default_factory=list, max_length=12)
    family_summary: bool = False
    prompts: dict[str, str] = Field(default_factory=dict)
    template: str = Field(default='', max_length=2000)
    operation: Literal['interval_days', 'completed_months'] | None = None
    date_field: Literal['probation_date', 'probation_end_date', 'official_date', 'resignation_date', 'birthday'] | None = None
    note: str = Field(min_length=1)

    @model_validator(mode='after')
    def allowed(self):
        from .slots import SLOT_LABELS
        allowed = {field for group in self.groups for field in GROUPS[group]} | (
            {'manager_name', 'manager_email'} if self.manager else set())
        if any(field not in allowed or field == 'families' for field in self.facts) or len(set(self.facts)) != len(self.facts):
            raise ValueError('invalid_context_facts')
        if self.family_summary and 'family' not in self.groups:
            raise ValueError('unavailable_family_context')
        for _, name, fmt, conversion in string.Formatter().parse(self.template):
            if name is not None and (name not in allowed or fmt or conversion):
                raise ValueError('invalid_context_placeholder')
        if self.mode == 'none' and (self.groups or self.manager or self.template or self.operation or self.facts or self.family_summary or self.prompts):
            raise ValueError('unexpected_context_requirements')
        if self.mode == 'calculation' and (self.operation is None or self.date_field is None):
            raise ValueError('missing_calculation_definition')
        if self.date_field is not None and self.date_field not in allowed:
            raise ValueError('unavailable_calculation_field')
        if len(set(self.groups)) != len(self.groups) or len(self.prompts) > 32:
            raise ValueError('invalid_context_requirements')
        if any(key not in SLOT_LABELS or not value.strip() or len(value) > 2000 for key, value in self.prompts.items()):
            raise ValueError('invalid_context_prompts')
        return self
