"""Strict, source-backed v3 evidence structures; no business inference."""
from datetime import date
from decimal import Decimal, InvalidOperation
import re

ROLES = {'definition', 'rule', 'procedure', 'inventory', 'detail', 'table', 'administrative'}


def validate(extraction, draft):
    from .document_packages import require
    segments = {s['segment_id']: s for s in extraction['segments']}

    def string(value):
        return isinstance(value, str) and bool(value.strip())

    def references(refs):
        require(isinstance(refs, list) and bool(refs), 'structured_references')
        for r in refs:
            require(isinstance(r, dict) and set(r) == {'segment_id', 'start', 'end'}, 'structured_reference')
            require(isinstance(r['segment_id'], str) and r['segment_id'] in segments, 'structured_segment')
            require(type(r['start']) is int and type(r['end']) is int and
                    0 <= r['start'] < r['end'] <= len(segments[r['segment_id']]['text']), 'structured_offset')

    def objects(name, fields):
        rows = draft[name]
        require(isinstance(rows, list), 'structured_array')
        ids = set()
        for row in rows:
            require(isinstance(row, dict) and set(row) == set(fields.split()), 'structured_fields')
            require(string(row['id']) and re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}', row['id'])
                    and row['id'] not in ids, 'structured_id')
            ids.add(row['id'])
            references(row['source_refs'])
        return {r['id']: r for r in rows}

    chunk_ids = set()
    for chunk in draft['chunks']:
        require(string(chunk.get('id')) and re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}', chunk['id'])
                and chunk['id'] not in chunk_ids and isinstance(chunk.get('role'), str)
                and chunk['role'] in ROLES, 'chunk_identity_role')
        chunk_ids.add(chunk['id'])
    scopes = objects('scopes', 'id name description item_ids chunk_ids coverage metric operations comparison source_refs')
    items = objects('items', 'id label name parent_id order chunk_ids source_refs')
    values = objects('values', 'id item_id name type value unit basis original conditions ambiguous source_refs')

    def links(value, allowed, nonempty=True):
        require(isinstance(value, list) and (bool(value) or not nonempty) and
                all(isinstance(x, str) and x in allowed for x in value)
                and len(set(value)) == len(value), 'structured_links')

    for scope in scopes.values():
        require(string(scope['name']) and string(scope['description']) and
                scope['coverage'] in ('complete', 'partial', 'unknown'), 'structured_scope')
        require(scope['metric'] is None or string(scope['metric']), 'structured_scope_metric')
        links(scope['operations'], {'list', 'count', 'procedure', 'compare', 'min', 'max'})
        require(scope['comparison'] in ('same_basis', 'nominal_amount'), 'structured_comparison')
        links(scope['item_ids'], items)
        links(scope['chunk_ids'], chunk_ids)
    for item in items.values():
        require(string(item['label']) and string(item['name']) and type(item['order']) is int
                and item['order'] >= 0, 'structured_item')
        require(item['parent_id'] is None or isinstance(item['parent_id'], str) and
                item['parent_id'] in items, 'structured_parent')
        links(item['chunk_ids'], chunk_ids)
    for item in items.values():
        seen = {item['id']}
        parent = item['parent_id']
        while parent is not None:
            require(parent not in seen, 'structured_cycle')
            seen.add(parent)
            parent = items[parent]['parent_id']
    for scope in scopes.values():
        members = set(scope['item_ids'])
        for sid in members:
            parent = items[sid]['parent_id']
            while parent is not None:
                require(parent not in members, 'scope_mixes_counting_levels')
                parent = items[parent]['parent_id']
    for value in values.values():
        require(isinstance(value['item_id'], str) and value['item_id'] in items and string(value['name']) and
                value['type'] in ('text', 'boolean', 'decimal', 'date') and string(value['original'])
                and isinstance(value['conditions'], str) and type(value['ambiguous']) is bool,
                'structured_value')
        require(all(v is None or string(v) for v in (value['unit'], value['basis'])), 'structured_unit')
        v = value['value']
        if value['type'] == 'decimal':
            require(string(v) and len(v) <= 100 and re.fullmatch(r'-?\d+(?:\.\d+)?', v)
                    and string(value['unit']) and string(value['basis']), 'structured_decimal')
            try:
                require(Decimal(v).is_finite(), 'structured_decimal')
            except InvalidOperation:
                require(False, 'structured_decimal')
        elif value['type'] == 'boolean':
            require(type(v) is bool, 'structured_boolean')
        elif value['type'] == 'date':
            try:
                require(string(v) and date.fromisoformat(v).isoformat() == v, 'structured_date')
            except (ValueError, TypeError):
                require(False, 'structured_date')
        else:
            require(string(v), 'structured_text')
    return {key: draft[key] for key in ('scopes', 'items', 'values')}
