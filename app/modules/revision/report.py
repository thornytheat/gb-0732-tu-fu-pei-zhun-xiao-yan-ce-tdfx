from app.manifest import canonical_json


def pairs_of(pairs, code):
    return [pair for pair in pairs if pair['sheet'] == code]


def sheet_changed(base, revised, code):
    """图幅内容是否变化：重估模型、配对集合任一不同即视为已变。"""
    if canonical_json(base['refit']['sheets'][code]) != canonical_json(revised['refit']['sheets'][code]):
        return True
    return canonical_json(pairs_of(base['pairing']['pairs'], code)) != canonical_json(
        pairs_of(revised['pairing']['pairs'], code))


def edge_diffs(base, revised):
    """逐条拼缝对比基底批次与修订批次，结果有差异的边即为重算边。"""
    before = {edge['code']: edge for edge in base['seams']['edges']}
    diffs = {}
    for edge in revised['seams']['edges']:
        prior = before[edge['code']]
        if canonical_json(prior) != canonical_json(edge):
            diffs[edge['code']] = {
                'code': edge['code'], 'a': edge['a'], 'b': edge['b'],
                'before': {'status': prior['status'], 'max_m': prior['max_m'], 'reason': prior['reason']},
                'after': {'status': edge['status'], 'max_m': edge['max_m'], 'reason': edge['reason']},
            }
    return diffs


def replaced_entries(rows, superseded, aliases):
    """每条补测记录：新观测值 + 被替换的原件行（源行号可回查）。"""
    by_key = {}
    for row in superseded:
        key = row['sheet'], aliases.get(row['name'], row['name'])
        by_key.setdefault(key, []).append(row)
    entries = []
    for row in rows:
        key = row['sheet'], aliases.get(row['name'], row['name'])
        originals = [{'kind': 'pixel' if mark['source']['file'] == 'pixels.csv' else 'ground',
                      'source': mark['source'], 'xy': mark['xy']}
                     for mark in sorted(by_key.get(key, []), key=lambda mark: mark['source']['id'])]
        entries.append({'sheet': row['sheet'], 'name': key[1], 'source': row['source'],
                        'pixel': row['pixel'], 'ground': row['ground'], 'superseded': originals})
    return entries


def build_report(base, revised, catalog, rows, superseded, aliases, supplement):
    edges = edge_diffs(base, revised)
    entries = replaced_entries(rows, superseded, aliases)
    signatures = {signature['sheet']: signature for signature in base['signatures']}
    base_reviews = {row['code']: row['status'] for row in base['reviews']}
    revised_reviews = {row['code']: row['status'] for row in revised['reviews']}
    sheets = []
    for specification in catalog['sheets']:
        code = specification['code']
        changed = sheet_changed(base, revised, code)
        incident = [diff for diff in edges.values() if code in (diff['a'], diff['b'])]
        signature = signatures.get(code)
        sheets.append({
            'code': code, 'title': specification['title'], 'changed': changed,
            'replaced': [entry for entry in entries if entry['sheet'] == code],
            'seams': incident,
            'review_before': base_reviews[code], 'review_after': revised_reviews[code],
            'signature': None if signature is None else {
                'reviewer': signature['reviewer'], 'signed_at': signature['signed_at'],
                'outcome': 'invalidated' if changed else 'carried',
            },
        })
    invalidated = [sheet for sheet in sheets if sheet['signature'] and sheet['signature']['outcome'] == 'invalidated']
    carried = [sheet for sheet in sheets if sheet['signature'] and sheet['signature']['outcome'] == 'carried']
    return {
        'base_run_id': base['manifest']['run_id'],
        'supplement': supplement,
        'sheets': sheets,
        'edges': list(edges.values()),
        'totals': {'resurvey_rows': len(rows), 'replaced_points': len(entries),
                   'superseded_rows': len(superseded), 'seams_recomputed': len(edges),
                   'signatures_invalidated': len(invalidated), 'signatures_carried': len(carried)},
    }


def carried_signatures(report, base):
    """未变化图幅的基底签审随修订沿用；失效签审只留在基底批次里回查。"""
    keep = {sheet['code'] for sheet in report['sheets']
            if sheet['signature'] and sheet['signature']['outcome'] == 'carried'}
    return [signature for signature in base['signatures'] if signature['sheet'] in keep]
