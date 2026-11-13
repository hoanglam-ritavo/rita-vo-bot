"""Compatibility entrypoint for the shared offline intent trainer."""
import sys
from src.intent import training

if __name__ == '__main__':
    raise SystemExit(training.main())
else:
    # Preserve existing imports and monkeypatch boundaries for offline callers.
    sys.modules[__name__] = training
