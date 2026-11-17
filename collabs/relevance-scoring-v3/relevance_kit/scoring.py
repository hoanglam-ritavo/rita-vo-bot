"""Versioned passage features and local scoring; no model, database or network IO."""
import hashlib
import json
import math
import re
import unicodedata
from functools import lru_cache

VERSION = 'passage-logistic-v1'
FEATURES = ('window_max', 'window_mean', 'window_std', 'window_count',
            'cosine_similarity', 'token_coverage', 'number_coverage', 'concept_coverage')


def scoring_digest(value):
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != 'digest'},
        sort_keys=True, separators=(',', ':'), ensure_ascii=False,
        allow_nan=False).encode('utf-8')).hexdigest()


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_scoring(value):
    if (not isinstance(value, dict)
            or set(value) != {'version', 'feature_names', 'means', 'scales', 'weights', 'bias', 'digest'}
            or value['version'] != VERSION or value['feature_names'] != list(FEATURES)
            or not _finite(value['bias'])):
        raise ValueError('invalid_relevance_scoring')
    for name in ('means', 'scales', 'weights'):
        items = value[name]
        if not isinstance(items, list) or len(items) != len(FEATURES) or not all(map(_finite, items)):
            raise ValueError('invalid_relevance_scoring')
    if (any(v <= 0 for v in value['scales']) or not isinstance(value['digest'], str)
            or value['digest'] != scoring_digest(value)):
        raise ValueError('invalid_relevance_scoring')
    return value


def apply_scoring(features, scoring):
    """Return finite logits, not calibrated probabilities or completeness claims."""
    validate_scoring(scoring)
    result = []
    for row in features:
        if len(row) != len(FEATURES) or not all(map(_finite, row)):
            raise ValueError('invalid_scoring_features')
        try:
            score = scoring['bias'] + math.fsum((v - mean) / scale * weight
                for v, mean, scale, weight in zip(row, scoring['means'],
                    scoring['scales'], scoring['weights']))
        except (OverflowError, ValueError):
            raise ValueError('invalid_scoring_features') from None
        if not math.isfinite(score):
            raise ValueError('invalid_scoring_features')
        result.append(score)
    return result


def _text(value):
    return ' '.join(unicodedata.normalize('NFC', value).casefold().split())


@lru_cache(maxsize=4096)
def _phrase_pattern(phrase):
    return re.compile(r'(?<!\w)' + re.escape(_text(phrase)) + r'(?!\w)')


def _phrase(phrase, text):
    return _phrase_pattern(phrase).search(text) is not None


def _coverage(query, passage):
    return len(query & passage) / len(query) if query else 1.0


def passage_features(question, content, window_scores, cosine_distance, concepts=()):
    """Eight fixed features; never inspect IDs, labels, ranks or an answer key."""
    if (not window_scores or not all(map(_finite, window_scores))
            or not _finite(cosine_distance)):
        raise ValueError('invalid_scoring_features')
    query, passage = _text(question), _text(content)
    words = lambda text: set(re.findall(r'\w+', text, flags=re.UNICODE))
    numbers = lambda text: set(re.findall(r'\d+(?:[.,]\d+)*', text))
    requested, matched = 0, 0
    for concept in concepts:
        phrases = [concept['canonical'], *concept['aliases']]
        def present(text):
            return (any(_phrase(p, text) for p in phrases)
                and (not concept['requires_any'] or any(_phrase(p, text) for p in concept['requires_any']))
                and not any(_phrase(p, text) for p in concept['excludes_any']))
        if present(query):
            requested += 1
            matched += present(passage)
    mean = math.fsum(window_scores) / len(window_scores)
    std = math.sqrt(math.fsum((score - mean) ** 2 for score in window_scores) / len(window_scores))
    features = [max(window_scores), mean, std, float(len(window_scores)),
        1.0 - cosine_distance, _coverage(words(query), words(passage)),
        _coverage(numbers(query), numbers(passage)), matched / requested if requested else 1.0]
    if not all(map(_finite, features)):
        raise ValueError('invalid_scoring_features')
    return features
