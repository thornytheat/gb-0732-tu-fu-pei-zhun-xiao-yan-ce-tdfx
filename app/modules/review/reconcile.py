from collections import Counter

from app.errors import RegisterError


def source_ids(rows, key='source'):
    return [row[key]['id'] for row in rows]


def from_file(rows, filename, key='source'):
    return [row[key]['id'] for row in rows if row[key]['file'] == filename]


def ledger(table, partitions):
    expected = Counter(source_ids(table['rows']) + source_ids(table['quarantine']))
    actual = Counter(identity for entries in partitions.values() for identity in entries)
    if expected != actual or table['records'] != sum(expected.values()):
        raise RegisterError('LEDGER_MISMATCH', '档件行号对账失败', 500, table['file'])
    return {'input': table['records'], **{name: len(rows) for name, rows in partitions.items()},
            'blank': table['blank'], 'repeated_headers': table['headers'], 'balanced': True}


def reconcile(intake, pairing, refit, seams):
    tables = intake['tables']
    pixels = [mark for mark in refit['quarantine'] if mark['source']['file'] == 'pixels.csv']
    ground = [mark for mark in refit['quarantine'] if mark['source']['file'] == 'ground.csv']
    result = {
        'controls': ledger(tables['pixels.csv'], {'accepted': from_file(refit['accepted'], 'pixels.csv', 'pixel_source'),
                                                  'outlier': from_file(refit['outliers'], 'pixels.csv', 'pixel_source'),
                                                  'isolated': source_ids(pixels)}),
        'ground': ledger(tables['ground.csv'], {'paired': from_file(pairing['pairs'], 'ground.csv', 'ground_source'),
                                                'isolated': source_ids(ground)}),
        'witnesses': ledger(tables['witness.csv'], {'eligible': [station['source']['id'] for edge in seams['edges']
                                                                 for station in edge['stations']],
                                                    'isolated': source_ids(seams['quarantine'])}),
        'reason_counts': dict(sorted(Counter(mark['reason'] for mark in
                                             refit['quarantine'] + seams['quarantine']).items())),
    }
    if 'resurvey.csv' in tables:
        resurvey = [mark for mark in refit['quarantine'] if mark['source']['file'] == 'resurvey.csv']
        result['resurvey'] = ledger(tables['resurvey.csv'],
                                    {'accepted': from_file(refit['accepted'], 'resurvey.csv', 'pixel_source'),
                                     'outlier': from_file(refit['outliers'], 'resurvey.csv', 'pixel_source'),
                                     'isolated': source_ids(resurvey)})
    return result
