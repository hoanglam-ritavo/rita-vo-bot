"""Inventory authored template coverage without reading application databases."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from src.artifact_authoring.sources import checked, read_group, recheck
from src.paths import REPOSITORY_ROOT

ASPECTS = ('overview', 'amount', 'conditions', 'paperwork', 'procedure')


def inventory(root=REPOSITORY_ROOT):
    captured = {}
    groups = {}
    for group in ('answers', 'retrieval', 'semantics'):
        groups[group], observed = read_group(root, group)
        captured.update(observed)

    routes_by_task = defaultdict(list)
    bindings_by_task = defaultdict(list)
    for route in groups['retrieval']['routes']:
        routes_by_task[route['task_id']].append(route)
    for binding in groups['retrieval']['bindings']:
        for task in binding['task_ids']:
            bindings_by_task[task].append(binding)
    counts = Counter()
    rows = []
    for card in groups['answers']['cards']:
        task_id = card['id']
        routes, bindings = routes_by_task[task_id], bindings_by_task[task_id]
        sections = {}
        for aspect in ASPECTS:
            section = card['sections'].get(aspect)
            state = ('undeclared' if section is None else 'declared_complete' if section['complete']
                     else 'declared_partial')
            counts[state] += 1
            sections[aspect] = {
                'state': state,
                'references': sorted({(ref['document_id'], ref['revision_id'], ref['chunk_index'])
                                      for unit in (section or {}).get('units', []) for ref in unit['refs']}),
            }
        rows.append({'task_id': task_id, 'sections': sections,
                     'route_ids': sorted({r['id'] for r in routes}),
                     'families': sorted({r.get('family', 'leave') for r in routes + bindings}),
                     'systems': sorted({r['system'] for r in routes + bindings if r.get('system')}),
                     'subtypes': sorted({r['subtype'] for r in routes if r.get('subtype')}),
                     'context_mode': card['context']['mode'], 'alias_count': len(card['aliases']),
                     'concept_ids': card['concept_ids']})
    recheck(captured)
    coverage = groups['answers']['coverage']
    return {'schema_version': 1, 'database_publication': 'not_checked', 'tasks': len(rows),
            'concepts': len(groups['semantics']['concepts']), 'sections': dict(counts),
            'chunk_dispositions': dict(Counter(row['disposition'] for row in coverage)),
            'unbound_chunks': [{'document_id': row['document_id'], 'chunk_index': row['chunk_index'],
                                'disposition': row['disposition']} for row in coverage
                               if row['disposition'] != 'template'],
            'inputs': {p.relative_to(root).as_posix(): hashlib.sha256(raw).hexdigest()
                       for p, raw in sorted(captured.items())}, 'rows': rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        output = args.output.absolute()
        if '..' in output.parts or not output.is_relative_to(REPOSITORY_ROOT / '.tmp'):
            raise ValueError('private_output_required')
        output = checked(output)
        report = inventory()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
        print(json.dumps({key: report[key] for key in ('tasks', 'concepts', 'sections', 'chunk_dispositions')}))
        return 0
    except (ValueError, OSError):
        print(json.dumps({'status': 'failed', 'code': 'coverage_inventory_failed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
