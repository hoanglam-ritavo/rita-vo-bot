from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from src.api.contracts import StrictModel, RetrieveRequest, ApiError


@dataclass(frozen=True)
class ConversationSettings:
    enabled: bool = False

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('invalid_conversation_configuration')

    def require_enabled(self):
        if not self.enabled:
            raise ApiError('conversation_disabled')


def from_environment(env):
    value = env.get('CONVERSATION_ENABLED', 'false').lower()
    if value not in ('true', 'false'):
        raise ValueError('invalid_conversation_configuration')
    return ConversationSettings(value == 'true')


class Identity(StrictModel):
    user_id: str

    @field_validator('user_id')
    @classmethod
    def uuid_identity(cls, value):
        try:
            return str(UUID(value))
        except ValueError:
            raise ValueError('invalid_user_id') from None


class ClarificationChoice(StrictModel):
    prompt_message_id: str
    option_id: int = Field(ge=1, le=5)

    @field_validator('prompt_message_id')
    @classmethod
    def uuid_prompt(cls, value):
        return Identity.uuid_identity(value)


class ConversationRequest(RetrieveRequest):
    user_id: str
    room_id: str
    message_id: str
    expected_context_version: int = Field(ge=0, le=2**53 - 1)
    text: str = Field(min_length=1, max_length=2000)
    choice: ClarificationChoice | None = None

    def stored_payload(self):
        # Preserve the existing fingerprint and retry payload for text-only turns.
        return self.model_dump(exclude={'choice'} if self.choice is None else None)

    @field_validator('user_id', 'room_id', 'message_id')
    @classmethod
    def uuid_identity(cls, value):
        return Identity.uuid_identity(value)


class RetryRequest(Identity):
    hrm_user_id: int = Field(gt=0, le=2**53 - 1)


class TurnResponse(StrictModel):
    room_id: str
    message_id: str
    sequence: int
    state: Literal['PROCESSING', 'COMPLETED', 'FAILED', 'CANCELLED', 'INTERRUPTED']
    context_version: int
    attempt: int
    result: dict | None = None
    error_code: str | None = None

    @model_validator(mode='after')
    def outcome(self):
        if (self.state == 'COMPLETED') != (self.result is not None):
            raise ValueError('invalid_turn_outcome')
        return self
