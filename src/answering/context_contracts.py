"""Versioned unified evidence. No personnel reads or model imports at module load."""
from typing import Annotated, Literal
from datetime import datetime

from pydantic import Field, model_validator
from src.api.base import StrictModel

Key = Annotated[str, Field(min_length=1, max_length=100, pattern=r'^\S+$')]
Text = Annotated[str, Field(min_length=1, max_length=2000)]
EmployeeField = Literal['employee_name','employee_code','job_title','job_role_name','department_name','work_email','phone','manager_name','manager_email','probation_date','probation_end_date','official_date','resignation_date','employee_type','contract_kind','birthday','families.name','families.birthday','families.relation_type','families.relation_type_display','social_insurance_number','social_insurance_date','health_insurance_number','health_clinic','identification_id','id_issue_date','id_issue_place','tax_id','bank_account_number','bank_account_name','bank_name','bank_branch']


class DocumentEvidence(StrictModel):
    kind: Literal['document']
    id: Key
    source_id: Key
    quote: str = Field(min_length=1, max_length=2000)


class EmployeeEvidence(StrictModel):
    kind: Literal['employee']
    id: Key
    field: EmployeeField
    subject: Annotated[str, Field(pattern=r'^(self|manager|family:[1-9][0-9]*)$')]
    label: Text
    value: Annotated[str, Field(max_length=2000)] | None
    observed_at: str
    updated_at: str | None

    @model_validator(mode='after')
    def timestamps(self):
        for value in (self.observed_at, self.updated_at):
            if value is not None and datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is None:
                raise ValueError('timestamp_timezone_required')
        return self


class InputEvidence(StrictModel):
    kind: Literal['user_input']
    id: Key
    slot: Key
    label: Text
    value: Text
    hypothetical: bool


class CalculationEvidence(StrictModel):
    kind: Literal['calculation']
    id: Key
    operation: Literal['interval_days', 'age_years', 'completed_months', 'multiply', 'add', 'tier', 'count']
    label: Text
    value: Text
    unit: Key
    input_refs: list[Key] = Field(min_length=1, max_length=12)
    rule_refs: list[Key] = Field(default_factory=list, max_length=12)
    as_of: str | None = None


Evidence = Annotated[DocumentEvidence | EmployeeEvidence | InputEvidence | CalculationEvidence,
                     Field(discriminator='kind')]


class UnifiedClaim(StrictModel):
    id: Key
    text: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[Key] = Field(min_length=1, max_length=12)


class MissingInput(StrictModel):
    slot: Key
    prompt: Text


class Application(StrictModel):
    mode: Literal['general', 'personalized', 'conditional', 'hypothetical'] = 'general'
    state: Literal['not_needed', 'applied', 'partial', 'unavailable', 'disabled', 'not_found', 'conflict'] = 'not_needed'
    missing_inputs: list[MissingInput] = Field(default_factory=list, max_length=32)
    conflicts: list[str] = Field(default_factory=list, max_length=12)


class EvidenceEnvelope(StrictModel):
    schema_version: Literal[2] = 2
    evidence: list[Evidence] = Field(default_factory=list, max_length=128)
    application: Application = Field(default_factory=Application)

    @model_validator(mode='after')
    def references(self):
        nodes = {item.id: item for item in self.evidence}
        if len(nodes) != len(self.evidence) or any(not key.strip() for key in nodes):
            raise ValueError('invalid_evidence_identity')
        sources = {source.source_id for source in getattr(self, 'sources', [])}
        for item in self.evidence:
            if isinstance(item, DocumentEvidence) and hasattr(self, 'sources') and item.source_id not in sources:
                raise ValueError('invalid_document_reference')
            if isinstance(item, CalculationEvidence):
                refs = item.input_refs + item.rule_refs
                if any(ref not in nodes or ref == item.id for ref in refs):
                    raise ValueError('invalid_calculation_reference')
                if any(not isinstance(nodes[ref], DocumentEvidence) for ref in item.rule_refs):
                    raise ValueError('invalid_rule_reference')
        visiting, visited = set(), set()
        def visit(key):
            if key in visiting:
                raise ValueError('cyclic_evidence')
            if key in visited:
                return
            visiting.add(key)
            node = nodes[key]
            if isinstance(node, CalculationEvidence):
                for ref in node.input_refs + node.rule_refs:
                    visit(ref)
            visiting.remove(key)
            visited.add(key)
        for key in nodes:
            visit(key)
        claims = getattr(self, 'claims', [])
        if len({c.id for c in claims}) != len(claims) or any(
                ref not in nodes for claim in claims for ref in claim.evidence_refs):
            raise ValueError('invalid_claim_reference')
        return self
