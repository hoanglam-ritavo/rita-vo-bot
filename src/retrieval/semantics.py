"""Reviewed, corpus-bound Vietnamese query expansion; no model or database imports."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from src.paths import REPOSITORY_ROOT

EXPANSION_VERSION = 'bounded-phrase-replacement-v1'
RERANK_QUERY_POLICY = 'original-with-existing-typos-v1'
STYLES = ('detail', 'conditions', 'procedure', 'enumeration_count', 'comparison')
MAX_ALTERNATES = 2
MAX_ARTIFACT_BYTES = 8_000_000


def normalized(value):
    return ' '.join(unicodedata.normalize('NFC', value).split())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class SemanticSettings:
    mode: str = 'off'
    artifact_path: Path | None = None

    def __post_init__(self):
        if self.mode not in ('off', 'expand'):
            raise ValueError('invalid_semantic_configuration')
        if self.artifact_path is not None:
            path = Path(self.artifact_path)
            object.__setattr__(self, 'artifact_path', path if path.is_absolute() else REPOSITORY_ROOT / path)
        if self.mode == 'expand' and self.artifact_path is None:
            raise ValueError('semantic_artifact_required')


def _phrases(values):
    return (isinstance(values, list) and len(values) <= 100
            and all(isinstance(item, str) and 0 < len(normalized(item)) <= 240 for item in values)
            and len({normalized(item).casefold() for item in values}) == len(values))


def validate_artifact(data, *, require_reviewed=True):
    """Validate shape/review declaration only; this cannot certify semantic accuracy."""
    fields = {'schema_version', 'reviewed', 'review_notes', 'package_fingerprint', 'concepts'}
    if (not isinstance(data, dict) or set(data) != fields
            or type(data['schema_version']) is not int or data['schema_version'] != 1
            or type(data['reviewed']) is not bool
            or not isinstance(data['review_notes'], str)
            or not isinstance(data['package_fingerprint'], str)
            or re.fullmatch('[0-9a-f]{64}', data['package_fingerprint']) is None
            or not isinstance(data['concepts'], list) or not 1 <= len(data['concepts']) <= 10_000):
        raise ValueError('invalid_semantic_artifact')
    if require_reviewed and (not data['reviewed'] or not data['review_notes'].strip()):
        raise ValueError('semantic_review_required')
    ids = set()
    required = {'id', 'canonical', 'aliases', 'requires_any', 'excludes_any'}
    for concept in data['concepts']:
        if (not isinstance(concept, dict) or not required <= set(concept)
                or set(concept) - required - {'styles'}
                or not isinstance(concept['id'], str)
                or re.fullmatch('[A-Za-z0-9._-]{1,100}', concept['id']) is None
                or concept['id'] in ids
                or not isinstance(concept['canonical'], str)
                or not 0 < len(normalized(concept['canonical'])) <= 240
                or not _phrases(concept['aliases']) or not concept['aliases']
                or not _phrases(concept['requires_any']) or not _phrases(concept['excludes_any'])
                or not isinstance(concept.get('styles', []), list)
                or any(style not in STYLES for style in concept.get('styles', []))
                or len(set(concept.get('styles', []))) != len(concept.get('styles', []))):
            raise ValueError('invalid_semantic_artifact')
        ids.add(concept['id'])
    return data


def _pattern(phrase):
    return re.compile(r'(?<!\w)' + re.escape(normalized(phrase)) + r'(?!\w)', re.IGNORECASE)


def phrase_matches(phrase, question):
    """Bounded matching shared by retrieval and authored answers."""
    return bool(_pattern(phrase).search(normalized(question)))


def matching_concepts(data, question):
    """Guarded language concepts, never document routing or authorization."""
    return {c['id'] for c in data['concepts']
            if (not c['requires_any'] or any(phrase_matches(p, question) for p in c['requires_any']))
            and not any(phrase_matches(p, question) for p in c['excludes_any'])
            and any(phrase_matches(p, question) for p in [c['canonical'], *c['aliases']])}


def _rules(data):
    targets = {}
    for concept in data['concepts']:
        for alias in concept['aliases']:
            targets.setdefault(normalized(alias).casefold(), set()).add(normalized(concept['canonical']).casefold())
    rules = []
    for concept in data['concepts']:
        canonical = normalized(concept['canonical'])
        for alias in concept['aliases']:
            alias = normalized(alias)
            if len(targets[alias.casefold()]) != 1 or alias.casefold() == canonical.casefold():
                continue
            rules.append((concept['id'], canonical, alias, _pattern(alias),
                          [_pattern(term) for term in concept['requires_any']],
                          [_pattern(term) for term in concept['excludes_any']],
                          frozenset(concept.get('styles', []))))
    return rules


def _expand(rules, question, styles):
    question = normalized(question)
    candidates = []
    style_set = set(styles) & set(STYLES)
    for identity, canonical, alias, pattern, required, excluded, evidence_styles in rules:
        if required and not any(term.search(question) for term in required):
            continue
        if any(term.search(question) for term in excluded):
            continue
        rewritten = pattern.sub(lambda _: canonical, question)
        if rewritten != question:
            candidates.append((-len(style_set.intersection(evidence_styles)),
                               -len(alias), identity, alias.casefold(), rewritten))
    seen, result = {question}, []
    for *_, rewritten in sorted(candidates):
        if rewritten not in seen:
            result.append(rewritten)
            seen.add(rewritten)
        if len(result) == MAX_ALTERNATES:
            break
    return result


def expansion_candidates(data, question, *, styles=()):
    """Replace bounded aliases only; preserve all surrounding conditions verbatim."""
    return _expand(_rules(data), question, styles)


class SemanticExpander:
    def __init__(self, settings=None):
        self.settings = settings or SemanticSettings()
        self.artifact = None
        self.fingerprint = None
        self._compiled = []

    def start(self):
        if self.settings.mode == 'off':
            return
        from src.semantics.corpus import decode_json, snapshot_fingerprint
        try:
            path = self.settings.artifact_path
            if path.stat().st_size > MAX_ARTIFACT_BYTES:
                raise ValueError('invalid_semantic_artifact')
            raw = path.read_bytes()
            data = validate_artifact(decode_json(raw))
            if data['package_fingerprint'] != snapshot_fingerprint():
                raise ValueError('semantic_package_mismatch')
            if path.read_bytes() != raw:
                raise ValueError('semantic_artifact_changed')
            self.artifact = data
            self.fingerprint = digest(data)
            self._compiled = _rules(data)
        except Exception as exc:
            self.close()
            code = str(exc) if isinstance(exc, ValueError) and str(exc) in {
                'invalid_semantic_artifact', 'semantic_review_required',
                'semantic_package_mismatch', 'semantic_artifact_changed'} else 'invalid_semantic_artifact'
            raise ValueError(code) from None

    def expansion(self, question, *, styles=()):
        return _expand(self._compiled, question, styles) if self.artifact else []

    def effective_fingerprint(self, base, *, intent_digest=None):
        if self.settings.mode == 'off':
            return base
        return digest({'base': base, 'semantic_artifact': self.fingerprint,
                       'expansion': EXPANSION_VERSION, 'rerank_query': RERANK_QUERY_POLICY,
                       'max_alternates': MAX_ALTERNATES, 'candidate_count': 30,
                       'fusion': 'rrf60', 'intent_assistance': intent_digest})

    def close(self):
        self.artifact = None
        self.fingerprint = None
        self._compiled = []
