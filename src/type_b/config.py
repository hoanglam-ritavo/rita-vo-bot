"""Opt-in internal caller boundary; secrets never appear in settings reprs."""
from dataclasses import dataclass


@dataclass(frozen=True)
class TypeBSettings:
    enabled: bool = False

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('invalid_type_b_configuration')


def from_environment(env):
    enabled = env.get('TYPE_B_ENABLED', 'false').lower()
    if enabled not in ('true', 'false'):
        raise ValueError('invalid_type_b_configuration')
    return TypeBSettings(enabled == 'true')
