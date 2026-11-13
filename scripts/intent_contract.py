"""Compatibility alias for shared intent dataset contracts."""
import sys
from src.intent import dataset

sys.modules[__name__] = dataset
