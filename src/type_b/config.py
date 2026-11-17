"""Opt-in internal caller boundary; secrets never appear in settings reprs."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TypeBSettings:
    enabled: bool = False
    service_key: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('invalid_type_b_configuration')
        if self.service_key is not None and (not isinstance(self.service_key, str) or not self.service_key.strip()):
            raise ValueError('invalid_type_b_service_key')
        if self.enabled and self.service_key is None:
            raise ValueError('type_b_service_key_required')


def from_environment(env):
    enabled = env.get('TYPE_B_ENABLED', 'false').lower()
    if enabled not in ('true', 'false'):
        raise ValueError('invalid_type_b_configuration')
    return TypeBSettings(enabled == 'true', env.get('TYPE_B_SERVICE_KEY'))
