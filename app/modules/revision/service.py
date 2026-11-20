from datetime import datetime, timezone
from hashlib import sha256
from time import perf_counter

from app.config import MAX_INPUT_BYTES, RULE_VERSION, RunOptions
from app.errors import RegisterError
from app.manifest import canonical_json, run_manifest
from app.modules.affine.service import fit_sheets
from app.modules.pairing.service import pair_controls
from app.modules.pairing.aliases import resolve_aliases
from app.modules.refit.service import refit_sheets
from app.modules.review.decisions import decisions
from app.modules.review.reconcile import reconcile
from app.modules.revision.report import build_report, carried_signatures
from app.modules.revision.supplement import LOGICAL_FILE, apply_supplement, convert_supplement, read_supplement
from app.modules.seams.service import check_seams
from app.pipeline import Survey

SAMPLE_FILE = 'resurvey.csv'


def load_sample_supplement(settings, dataset):
    path = settings.sample_root / dataset / SAMPLE_FILE
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
    except OSError as error:
        raise RegisterError('RESURVEY_UNAVAILABLE', '该图册没有登记的补测表', 404, SAMPLE_FILE) from error
    if len(raw) > MAX_INPUT_BYTES:
        raise RegisterError('SOURCE_TOO_LARGE', '档件超过单表上限', 413, SAMPLE_FILE)
    return raw


def resolve_supplement(settings, form, dataset):
    if form.content is None:
        return load_sample_supplement(settings, dataset), SAMPLE_FILE
    raw = form.content.encode('utf-8')
    if len(raw) > MAX_INPUT_BYTES:
        raise RegisterError('SOURCE_TOO_LARGE', '档件超过单表上限', 413, form.filename)
    return raw, form.filename


def revision_id(base_run_id, supplement_hash):
    fingerprint = {'kind': 'revision', 'base_run_id': base_run_id,
                   'supplement': supplement_hash, 'rule_version': RULE_VERSION}
    return sha256(canonical_json(fingerprint).encode()).hexdigest()


def run_revision(settings, store, base_run_id, form):
    started = perf_counter()
    base = store.get(base_run_id)
    if base['manifest'].get('kind') == 'revision':
        raise RegisterError('REVISION_BASE_INVALID', '修订批次不能作为基底，请基于原始校验批次生成修订', 409,
                            base['manifest']['base_run_id'])
    options = RunOptions(**base['manifest']['parameters'])
    survey = Survey(settings, options)
    original_inputs = {name: digest for name, digest in base['manifest']['inputs'].items()
                       if name != LOGICAL_FILE}
    if run_manifest(survey, 0)['inputs'] != original_inputs:
        raise RegisterError('BASE_INPUTS_CHANGED', '基底批次对应的档件已变化，请先重新运行校验', 409)
    raw, filename = resolve_supplement(settings, form, options.dataset)
    supplement_hash = sha256(raw).hexdigest()
    run_id = revision_id(base_run_id, supplement_hash)
    existing = store.get(run_id, required=False)
    if existing is not None:
        return existing, True

    # 以下任何一步失败都在写库之前抛出，基底批次保持原样。
    table = read_supplement(raw)
    rows = convert_supplement(table, survey.catalog)
    aliases = resolve_aliases(survey.catalog['aliases'])
    coordinates, superseded = apply_supplement(survey.coordinates, rows, aliases)
    intake = {'catalog': survey.intake['catalog'],
              'tables': {**survey.intake['tables'], LOGICAL_FILE: table}}
    pairing = pair_controls(coordinates, survey.catalog)
    affine = fit_sheets(pairing, survey.catalog)
    refit = refit_sheets(pairing, affine, survey.catalog, options.control_limit_m)
    seams = check_seams(intake, coordinates, pairing, refit, options.seam_limit_m)
    ledger = reconcile(intake, pairing, refit, seams)
    reviews = decisions(survey.catalog, refit, seams)
    revised = {'pairing': pairing, 'refit': refit, 'seams': seams, 'reviews': reviews}
    supplement = {'file': filename, 'logical_file': LOGICAL_FILE, 'sha256': supplement_hash,
                  'rows': len(rows)}
    report = build_report(base, revised, survey.catalog, rows, superseded, aliases, supplement)
    manifest = {'run_id': run_id, 'kind': 'revision', 'base_run_id': base_run_id,
                'inputs': {**base['manifest']['inputs'], LOGICAL_FILE: supplement_hash},
                'parameters': options.model_dump(), 'rule_version': RULE_VERSION,
                'data_version': survey.catalog['version'],
                'created_at': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                'processing_ms': round((perf_counter() - started) * 1000, 3)}
    snapshot = {'manifest': manifest, 'catalog': survey.catalog, 'ledger': ledger, 'reviews': reviews,
                'revision': report, 'intake': intake['tables'], 'coordinates': coordinates,
                'pairing': pairing, 'affine': affine, 'refit': refit, 'seams': seams}
    return store.insert(snapshot, carried_signatures(report, base))
