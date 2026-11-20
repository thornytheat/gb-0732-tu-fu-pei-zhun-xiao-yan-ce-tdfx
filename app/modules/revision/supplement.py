from app.errors import RegisterError
from app.modules.coordinates.numbers import clean, failure, point_name
from app.modules.coordinates.projection import ground_position, pixel_position
from app.modules.intake.decode import decode_table
from app.modules.intake.reader import read_rows
from app.reasons import finding

# 补测表在册内的逻辑表名；上传时的原始文件名只登记在清单里，不参与行号对账。
LOGICAL_FILE = 'resurvey.csv'


def read_supplement(raw):
    text, encoding = decode_table(raw, LOGICAL_FILE)
    table = read_rows(text, LOGICAL_FILE, encoding)
    if table['state'] != 'RECEIVED':
        raise RegisterError('RESURVEY_EMPTY', '补测表没有可用记录', evidence=table['state'])
    if table['quarantine']:
        raise RegisterError('RESURVEY_ROW_INVALID', '补测表存在结构隔离行，整批不予修订',
                            evidence=[mark['source']['id'] for mark in table['quarantine']])
    return table


def convert_supplement(table, catalog):
    sheets = {sheet['code']: sheet for sheet in catalog['sheets']}
    origin = (catalog['longitude_deg'], catalog['latitude_deg'])
    rows, problems = [], []
    for row in table['rows']:
        sheet, name = clean(row['sheet']).upper(), point_name(row['name'])
        try:
            if sheet not in sheets:
                raise failure('UNKNOWN_SHEET')
            if not name:
                raise failure('EMPTY_NAME')
            pixel = pixel_position(row['u'], row['v'], sheets[sheet])
            ground = ground_position(row, origin)
        except RegisterError as error:
            problems.append({'source': row['source']['id'], 'reason': error.code})
            continue
        rows.append({'source': row['source'], 'sheet': sheet, 'name': name,
                     'pixel': pixel, 'ground': ground})
    if problems:
        raise RegisterError('RESURVEY_ROW_INVALID', '补测表存在无法换算的记录，整批不予修订', evidence=problems)
    return rows


def apply_supplement(converted, rows, aliases):
    """把补测观测覆盖到换算结果上；原件行不删改，全部转入隔离并保留源行号。"""
    def canonical(row):
        return row['sheet'], aliases.get(row['name'], row['name'])

    resurvey = {}
    for row in rows:
        key = canonical(row)
        if key in resurvey:
            raise RegisterError('RESURVEY_DUPLICATE', '补测表内同一图幅点名重复，整批不予修订',
                                evidence={'sheet': key[0], 'name': key[1]})
        resurvey[key] = row
    original_keys = {canonical(row) for pool in (converted['pixels'], converted['ground']) for row in pool}
    unknown = sorted(set(resurvey) - original_keys)
    if unknown:
        raise RegisterError('RESURVEY_UNKNOWN_POINT', '补测点在原档中没有同名记录，整批不予修订',
                            evidence=[{'sheet': sheet, 'name': name} for sheet, name in unknown])

    keep, superseded = {'pixels': [], 'ground': []}, []
    for kind in ('pixels', 'ground'):
        for row in converted[kind]:
            (superseded if canonical(row) in resurvey else keep[kind]).append(row)
    quarantine = list(converted['quarantine'])
    for row in superseded:
        sheet, name = canonical(row)
        quarantine.append(finding(row['source'], 'SUPERSEDED_BY_RESURVEY', sheet=sheet, name=name,
                                  resurvey=resurvey[(sheet, name)]['source']['id']))
    for row in rows:
        keep['pixels'].append({'source': row['source'], 'sheet': row['sheet'], 'name': row['name'], 'xy': row['pixel']})
        keep['ground'].append({'source': row['source'], 'sheet': row['sheet'], 'name': row['name'], 'xy': row['ground']})
    revised = {'pixels': keep['pixels'], 'ground': keep['ground'], 'quarantine': quarantine,
               'counts': converted['counts']}
    return revised, superseded
