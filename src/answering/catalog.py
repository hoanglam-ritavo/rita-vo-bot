"""Task/evidence structure and lexical pairing; DB references validate per claim."""
import hashlib
from pathlib import Path
from typing import Literal
from pydantic import Field
from src.paths import REPOSITORY_ROOT
from src.retrieval.semantics import validate_artifact
from src.semantics.corpus import decode_json
from .contracts import Strict

METADATA = ('title', 'applicability', 'effective_from', 'effective_to', 'version_label', 'source_path')


def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


class Reference(Strict):
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    document_id: str
    revision_id: str
    chunk_index: int = Field(ge=0)
    content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    quote: str = Field(min_length=1, max_length=2000)
    metadata: dict[str, str | None]


class Unit(Strict):
    id: str = Field(pattern=r'^[a-z0-9.-]{1,100}$')
    text: str = Field(min_length=1, max_length=2000)
    refs: list[Reference] = Field(min_length=1, max_length=5)


class Section(Strict):
    complete: bool
    units: list[Unit] = Field(min_length=1, max_length=12)


class Card(Strict):
    source_type: Literal['type_a']
    clarification: str = Field(min_length=1, max_length=500)
    id: str = Field(pattern=r'^[a-z0-9.-]{1,100}$')
    label: str = Field(min_length=1, max_length=240)
    concept_ids: list[str]
    aliases: list[str] = Field(min_length=1, max_length=100)
    requires_any: list[str]
    excludes_any: list[str]
    sections: dict[Literal['overview', 'amount', 'conditions', 'paperwork', 'procedure'], Section]


class Coverage(Strict):
    document_id: str
    chunk_index: int = Field(ge=0)
    disposition: Literal['template', 'excerpt', 'unavailable']
    note: str = Field(min_length=1)


class Catalog(Strict):
    schema_version: Literal[2]
    package_fingerprint: str
    lexicon_sha256: str
    cards: list[Card]
    coverage: list[Coverage]


def validate_catalog(data, lexicon, lexicon_sha256, snapshot=None, *, root=REPOSITORY_ROOT):
    catalog = Catalog.model_validate(data)
    if catalog.lexicon_sha256 != lexicon_sha256:
        raise ValueError('answer_catalog_identity_mismatch')
    concepts = {c['id'] for c in lexicon['concepts']}
    ids, used, unit_ids = set(), set(), {}
    for card in catalog.cards:
        if (card.id in ids or not card.sections or not set(card.concept_ids) <= concepts
                or any(not p.strip() or len(p) > 240 for p in card.aliases + card.requires_any + card.excludes_any)):
            raise ValueError('invalid_answer_card')
        ids.add(card.id)
        for section in card.sections.values():
            for unit in section.units:
                if unit.id in unit_ids and unit_ids[unit.id] != unit.model_dump():
                    raise ValueError('conflicting_evidence_unit')
                unit_ids[unit.id] = unit.model_dump()
                if not unit.text.strip():
                    raise ValueError('empty_answer_unit')
                for ref in unit.refs:
                    key = (ref.document_id, ref.chunk_index)
                    if (not ref.quote.strip() or set(ref.metadata) != set(METADATA)
                            or not ref.document_id or not ref.revision_id):
                        raise ValueError('invalid_answer_reference')
                    used.add(key)
    coverage = {(c.document_id, c.chunk_index): c for c in catalog.coverage}
    expected = {(c['document_id'], c['chunk_index']) for c in snapshot['chunks']} if snapshot is not None else None
    if (len(coverage) != len(catalog.coverage) or (expected is not None and set(coverage) != expected)
            or any((row.disposition == 'template') != (key in used) for key, row in coverage.items())):
        raise ValueError('incomplete_answer_inventory')
    return catalog


def load_catalog(path, lexicon_path):
    path, lexicon_path = Path(path), Path(lexicon_path)
    if path.stat().st_size > 16_000_000 or lexicon_path.stat().st_size > 8_000_000:
        raise ValueError('answer_artifact_too_large')
    raw, lexical = path.read_bytes(), lexicon_path.read_bytes()
    lexicon = validate_artifact(decode_json(lexical))
    catalog = validate_catalog(decode_json(raw), lexicon, sha_bytes(lexical))
    if path.read_bytes() != raw or lexicon_path.read_bytes() != lexical:
        raise ValueError('answer_artifact_changed')
    return catalog, lexicon
