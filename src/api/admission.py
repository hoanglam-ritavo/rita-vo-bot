"""Single request ownership on the API service's event loop."""
from .contracts import ApiError


def try_admit(owner):
    if owner.busy:
        return False
    owner.busy = True
    owner._idle.clear()
    return True


def admit(owner):
    if not try_admit(owner):
        raise ApiError('busy', 429)


def release(owner):
    owner.busy = False
    owner._idle.set()
