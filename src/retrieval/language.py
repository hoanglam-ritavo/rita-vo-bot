"""Bounded discourse handling shared by deterministic document consumers."""
import re


def positive_question(question):
    """Honor an explicit contrast without turning ordinary negation positive."""
    parts = re.split(r'[,;]\s*kh(?:ô|o)ng\s+ph(?:ả|a)i\s+', question, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2 and not re.search(r'\b(?:không|khong|chưa|chua)\b', parts[0], re.IGNORECASE):
        return parts[0].strip()
    return question
