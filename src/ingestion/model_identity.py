"""Pure validation of declared model revision identities; no runtime loading."""
import argparse
import re


def parse_model_revision(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-fA-F]{40}', value):
        raise ValueError('Expected full 40-hex model commit.')
    return value.lower()


def model_commit(value):
    """Argparse adapter preserving the existing command error message."""
    try:
        return parse_model_revision(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
