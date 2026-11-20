from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import shutil
import sqlite3

import pytest

from app.config import ROOT, RunOptions
from app.modules.review.service import run_review
from app.modules.revision.handler import RevisionInput
from app.modules.revision.service import run_revision
from app.store import Store
from conftest import by_key

HEADER = '图幅,点名,像素列,像素行,坐标系,单位,横坐标,纵坐标,备注'
ONLY_0421 = f'{HEADER}\n0421,冲突地面,415,676,LOCAL-ENU,m,317.64,516.18,补测定值\n'


def make_base(client, sign_sheet='0431'):
    base = client.post('/api/review/runs', json={}).json()
    if sign_sheet:
        url = f"/api/review/runs/{base['manifest']['run_id']}/signatures"
        assert client.post(url, json={'sheet': sign_sheet, 'reviewer': '周测绘'}).status_code == 200
    return client.get(f"/api/review/runs/{base['manifest']['run_id']}").json()


def revise(client, base, payload=None):
    if payload is None:
        payload = {}
    return client.post(f"/api/review/runs/{base['manifest']['run_id']}/revisions", json=payload)


def test_revision_replaces_conflicts_and_reports_per_sheet(client):
    base = make_base(client)
    response = revise(client, base)
    assert response.status_code == 200
    revision = response.json()
    assert revision['reused'] is False
    manifest = revision['manifest']
    assert (manifest['kind'], manifest['base_run_id']) == ('revision', base['manifest']['run_id'])
    assert manifest['parameters'] == base['manifest']['parameters']
    sample = (ROOT / 'sample_data' / 'field' / 'resurvey.csv').read_bytes()
    assert manifest['inputs']['resurvey.csv'] == sha256(sample).hexdigest()

    report = revision['revision']
    assert report['totals'] == {'resurvey_rows': 4, 'replaced_points': 4, 'superseded_rows': 10,
                                'seams_recomputed': 2, 'signatures_invalidated': 1, 'signatures_carried': 0}
    sheets = {sheet['code']: sheet for sheet in report['sheets']}
    hexi = sheets['0421']
    assert hexi['changed'] is True
    conflict = by_key(hexi['replaced'], '冲突地面', key='name')
    assert [row['kind'] for row in conflict['superseded']] == ['ground', 'ground', 'pixel']
    assert [row['source']['id'] for row in conflict['superseded']] == ['ground.csv:14', 'ground.csv:15',
                                                                       'pixels.csv:18']
    assert conflict['source']['file'] == 'resurvey.csv'
    p08 = by_key(hexi['replaced'], 'P08', key='name')
    assert p08['ground'] == [636.68, 504.55]
    assert {edge['code'] for edge in hexi['seams']} == {'HX-QS', 'HX-LW'}
    assert sheets['0431']['signature'] == {'outcome': 'invalidated', 'reviewer': '周测绘',
                                           'signed_at': base['signatures'][0]['signed_at']}
    assert (sheets['0431']['review_before'], sheets['0431']['review_after']) == ('READY', 'READY')
    assert sheets['0432']['changed'] is False
    edges = {edge['code']: edge for edge in report['edges']}
    assert edges['HX-QS']['before']['status'] == edges['HX-QS']['after']['status'] == 'FAIL'
    assert edges['HX-QS']['before']['max_m'] != edges['HX-QS']['after']['max_m']
    assert 'QS-SJ' not in edges

    assert revision['signatures'] == []
    assert revision['ledger']['resurvey'] == {'input': 4, 'accepted': 4, 'outlier': 0, 'isolated': 0,
                                              'blank': 0, 'repeated_headers': 0, 'balanced': True}
    assert revision['ledger']['controls']['isolated'] == 19
    assert revision['ledger']['reason_counts']['SUPERSEDED_BY_RESURVEY'] == 10
    superseded = [mark for mark in revision['refit']['quarantine'] if mark['reason'] == 'SUPERSEDED_BY_RESURVEY']
    assert {mark['source']['file'] for mark in superseded} == {'pixels.csv', 'ground.csv'}
    assert revision['refit']['outliers'] == []
    p08_revised = next(row for row in revision['refit']['accepted'] if row['name'] == 'P08')
    assert p08_revised['pixel_source']['file'] == 'resurvey.csv'


def test_base_batch_stays_intact_and_lists_revisions(client):
    base = make_base(client)
    revision = revise(client, base).json()
    reread = client.get(f"/api/review/runs/{base['manifest']['run_id']}").json()
    assert reread['signatures'] == base['signatures']
    assert [row['status'] for row in reread['reviews']] == ['RETURN', 'RETURN', 'READY', 'HOLD']
    assert 'revision' not in reread
    assert [item['run_id'] for item in reread['revisions']] == [revision['manifest']['run_id']]
    assert reread['ledger']['controls']['accepted'] == 35


def test_same_resurvey_rerun_reuses_revision_without_new_version(client):
    base = make_base(client)
    first = revise(client, base).json()
    again = revise(client, base).json()
    assert again['reused'] is True
    assert again['manifest']['run_id'] == first['manifest']['run_id']
    assert client.get(f"/api/review/runs/{first['manifest']['run_id']}").json()['signatures'] == []
    with sqlite3.connect(client.app.state.settings.database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM runs').fetchone() == (2,)
        assert connection.execute('SELECT COUNT(*) FROM signatures').fetchone() == (1,)


def test_concurrent_identical_revisions_store_one_version(settings):
    store = Store(settings.database)
    store.initialize()
    base, _ = run_review(settings, store, RunOptions())
    form = RevisionInput()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: run_revision(settings, store, base['manifest']['run_id'], form),
                                range(4)))
    assert sum(not reused for _, reused in results) == 1
    assert len({value['manifest']['run_id'] for value, _ in results}) == 1
    with sqlite3.connect(settings.database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM runs').fetchone() == (2,)


@pytest.mark.parametrize('content, code', [
    (f'{HEADER}\n0421,冲突地面,415,676,LOCAL-ENU,m,317.64,516.18,"未闭合\n', 'MALFORMED_CSV'),
    (f'{HEADER}\n0999,冲突地面,415,676,LOCAL-ENU,m,317.64,516.18,错幅\n', 'RESURVEY_ROW_INVALID'),
    (f'{HEADER}\n0421,冲突地面,415,676,LOCAL-ENU,m,abc,516.18,坏数\n', 'RESURVEY_ROW_INVALID'),
    (f'{HEADER}\n0421,新点,415,676,LOCAL-ENU,m,317.64,516.18,原档无此点\n', 'RESURVEY_UNKNOWN_POINT'),
    (f'{HEADER}\n0421,P04,415,676,LOCAL-ENU,m,317.64,516.18,甲\n'
     '0421,河堤四号,415,676,LOCAL-ENU,m,317.64,516.18,乙\n', 'RESURVEY_DUPLICATE'),
    (f'{HEADER}\n', 'RESURVEY_EMPTY'),
])
def test_invalid_supplement_fails_whole_revision_and_keeps_base(client, content, code):
    base = make_base(client)
    response = revise(client, base, {'filename': 'bad.csv', 'content': content})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == code
    reread = client.get(f"/api/review/runs/{base['manifest']['run_id']}").json()
    assert reread['signatures'] == base['signatures']
    assert reread['revisions'] == []
    with sqlite3.connect(client.app.state.settings.database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM runs').fetchone() == (1,)


def test_failed_revision_leaves_old_version_then_retry_succeeds(client):
    base = make_base(client)
    bad = revise(client, base, {'content': f'{HEADER}\n0421,冲突地面,415,676,LOCAL-ENU,m,abc,1,坏\n'})
    assert bad.status_code == 422
    good = revise(client, base)
    assert good.status_code == 200 and good.json()['reused'] is False
    assert client.get(f"/api/review/runs/{base['manifest']['run_id']}").json()['signatures'] == base['signatures']


def test_signature_on_unchanged_sheet_is_carried_and_locked(client):
    base = make_base(client)
    revision = revise(client, base, {'content': ONLY_0421}).json()
    report = revision['revision']
    assert report['totals']['signatures_carried'] == 1
    assert report['totals']['signatures_invalidated'] == 0
    carried = by_key(report['sheets'], '0431')
    assert carried['signature']['outcome'] == 'carried'
    assert revision['signatures'] == base['signatures']
    url = f"/api/review/runs/{revision['manifest']['run_id']}/signatures"
    same = client.post(url, json={'sheet': '0431', 'reviewer': '周测绘', 'expected_version': 0})
    assert same.status_code == 200 and same.json()['signed_at'] == base['signatures'][0]['signed_at']
    stale = client.post(url, json={'sheet': '0431', 'reviewer': '陈测绘', 'expected_version': 0})
    assert stale.status_code == 409
    assert client.get(f"/api/review/runs/{revision['manifest']['run_id']}").json()['signatures'] == base['signatures']


def test_changed_sheet_can_be_resigned_on_revision_only(client):
    base = make_base(client)
    revision = revise(client, base).json()
    url = f"/api/review/runs/{revision['manifest']['run_id']}/signatures"
    signed = client.post(url, json={'sheet': '0431', 'reviewer': '陈测绘', 'expected_version': 0})
    assert signed.status_code == 200
    assert client.get(f"/api/review/runs/{revision['manifest']['run_id']}").json()['signatures'] == [signed.json()]
    assert client.get(f"/api/review/runs/{base['manifest']['run_id']}").json()['signatures'] == base['signatures']


def test_revision_export_keeps_report_and_reason_dictionary(client):
    base = make_base(client, sign_sheet=None)
    revision = revise(client, base).json()
    run_id = revision['manifest']['run_id']
    bundle = client.get(f'/api/review/runs/{run_id}/export').json()
    assert bundle['revision']['base_run_id'] == base['manifest']['run_id']
    assert bundle['revision']['supplement']['rows'] == 4
    assert 'SUPERSEDED_BY_RESURVEY' in bundle['reason_dictionary']
    assert set(bundle['manifest']['inputs']) == {'catalog.json', 'pixels.csv', 'ground.csv', 'witness.csv',
                                                 'resurvey.csv'}


def test_revision_rejects_revision_as_base(client):
    base = make_base(client, sign_sheet=None)
    revision = revise(client, base).json()
    nested = client.post(f"/api/review/runs/{revision['manifest']['run_id']}/revisions", json={})
    assert (nested.status_code, nested.json()['detail']['code']) == (409, 'REVISION_BASE_INVALID')
    assert nested.json()['detail']['evidence'] == base['manifest']['run_id']


def test_revision_of_unknown_base_is_404(client):
    response = client.post('/api/review/runs/' + '0' * 64 + '/revisions', json={})
    assert (response.status_code, response.json()['detail']['code']) == (404, 'RUN_NOT_FOUND')


def test_changed_source_files_block_revision(client, settings, tmp_path):
    copied = tmp_path / 'inputs'
    shutil.copytree(settings.sample_root, copied)
    settings.sample_root = copied
    base = client.post('/api/review/runs', json={}).json()
    with (copied / 'field' / 'pixels.csv').open('ab') as file:
        file.write(b'\n')
    response = revise(client, base)
    assert (response.status_code, response.json()['detail']['code']) == (409, 'BASE_INPUTS_CHANGED')


def test_resurvey_sample_endpoint(client):
    sample = client.get('/api/review/resurvey-sample?dataset=field')
    assert sample.status_code == 200
    assert sample.json()['filename'] == 'resurvey.csv'
    assert '冲突地面' in sample.json()['content']
    assert client.get('/api/review/resurvey-sample?dataset=reference').status_code == 404


def test_revision_inherits_base_thresholds(client):
    base = client.post('/api/review/runs', json={'seam_limit_m': 3}).json()
    assert by_key(base['reviews'], '0421')['status'] == 'READY'
    revision = revise(client, base).json()
    assert revision['manifest']['parameters']['seam_limit_m'] == 3
    assert by_key(revision['reviews'], '0421')['status'] == 'READY'


def test_home_lists_revision_section(client):
    assert '修订批次' in client.get('/').text
