"""Bound public-only inputs for the discounted-notice experiment; no solves."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
import math
from pathlib import Path
import platform
from importlib.metadata import version
from scipy.optimize._highspy import _core as highs_core

from .allocation_v3 import canonical
from .core import Demand, Lane, Order, Recipe, Stock
from .core_v3 import ContractV3, NetworkV3
from .freeze_v3_design import verify_freeze
from .networks_v3 import V3PublicCase

ALPHAS = (0.25, 0.5, 0.75)
PLANNER_HASH = '48f341a686809e41efa15b41583ccab562652ab9ae8a2bc24dbff97dcfcb239c'
DESIGN_HASH = 'bb0a2208ca1668198cdf09ff091d8cdf37fcbfbe0ec9baf8f5897cc0f51ed224'
DESIGN_VERIFY_HASH = '7a0c0f4218632c47c75b511f76b8a19df0d0c42bbf06b0cbf77f969ade2a3ce1'
PROFILE_CAPS = dict(paths=30, periods_per_path=20, maximum_mip_phases=600,
                    per_mip_seconds=30, wall_seconds=600, peak_rss_mib=512,
                    saved_bytes=134217728, retries=0)
SOURCES = (
    'src/sn/__init__.py', 'src/sn/allocation_v3.py', 'src/sn/core.py',
    'src/sn/core_v2.py', 'src/sn/core_v3.py', 'src/sn/handoff_v3_single.py',
    'src/sn/networks_v3.py', 'src/sn/freeze_v3_design.py',
    'src/sn/planner_v3_single_r3.py', 'src/sn/belief_v3_common.py',
    'src/sn/belief_v3.py', 'src/sn/verify_belief_v3.py',
)
EVIDENCE = (
    'configs/proposed-v3-allocation.json', 'configs/v3-design/selection-freeze.json',
    'configs/v3-design/manifest.json',
    'results/v3_truthful_calibration_r4/result.json',
    'results/v3_truthful_calibration_r4/verification.json',
    'results/v3_truthful_calibration_r4/progress.jsonl',
    'results/v3_design_false_notice_r2/result.json',
    'results/v3_design_false_notice_r2/verification.json',
    'results/v3_design_false_notice_r2/progress.jsonl',
    'docs/PROPOSED_V3_PROTOCOL_RECONCILED.md', 'docs/V3_SINGLE_SOLVE_R3_PROTOCOL.md',
    'docs/V3_PROTOCOL_R6_FOUR_CELL_DESIGN.md',
    'docs/paper_prep/revision_r2/DISCOUNTED_BELIEF_PROPOSAL.md',
    'docs/paper_prep/discounted_belief_r1/EXECUTION_SPEC.md',
    'tests/test_v3_discounted_belief.py',
)


def digest(path: Path) -> str:
    h = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path: Path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    raw = path.read_bytes()
    value = json.loads(raw, object_pairs_hook=unique)
    if raw != canonical(value):
        raise ValueError('noncanonical input: ' + str(path))
    return value


def manifest_path(root: Path, mode: str) -> Path:
    if mode not in ('profile', 'study'):
        raise ValueError('mode must be profile or study')
    return root / f'configs/proposed-v3-discounted-belief-{mode}-r1.json'


def paths(mode: str) -> dict:
    base = f'results/v3_discounted_belief_{mode}_r1'
    return {'output_root': base, 'progress': base + '/progress.jsonl',
            'result': base + '/result.json', 'verification': base + '/verification.json',
            'attempt_marker': base + '/attempt.started',
            'first_solve_marker': base + '/first_solve.started',
            'approval_receipt': f'configs/owner-approvals/v3-discounted-belief-{mode}-r1.json'}


def receipt(root: Path, manifest: dict, manifest_hash: str) -> None:
    want = {'schema': 1, 'decision': manifest['receipt_decision'],
            'execution_manifest_sha256': manifest_hash}
    path = root / manifest['paths']['approval_receipt']
    if not path.is_file() or read(path) != want:
        raise PermissionError('owner receipt must name the exact reviewed execution-manifest hash')


def load_case(root: Path, item: dict) -> V3PublicCase:
    """Hydrate the already generated public JSON; never generate a network."""
    case_id = item['case_id']
    if '/' in case_id or not case_id.startswith('v3-c0-'):
        raise ValueError('not a frozen rotation-0 public ID')
    base = (root / 'configs/v3-design/cases/candidate-0').resolve()
    path = (base / (case_id + '.json')).resolve()
    if path.parent != base or digest(path) != item['content_sha256']:
        raise ValueError('public content hash or location differs: ' + case_id)
    raw = read(path)
    net = dict(raw['network'])
    for key in ('sites', 'products'):
        net[key] = tuple(net[key])
    net['initial_stock'] = tuple(Stock(**v) for v in net['initial_stock'])
    net['initial_outstanding'] = tuple(Order(**v) for v in net['initial_outstanding'])
    net['lanes'] = tuple(Lane(**v) for v in net['lanes'])
    net['recipes'] = tuple(Recipe(**{**v, 'inputs': tuple(tuple(x) for x in v['inputs'])})
                           for v in net['recipes'])
    case = V3PublicCase(**{**raw, 'network': NetworkV3(**net),
        'contract': ContractV3(**raw['contract']),
        'short_capacity': tuple(raw['short_capacity']), 'long_capacity': tuple(raw['long_capacity']),
        'baseline_demand': tuple(tuple(Demand(**v) for v in row) for row in raw['baseline_demand'])})
    if sha256(canonical(asdict(case))).hexdigest() != item['content_sha256']:
        raise ValueError('public deserialization differs: ' + case_id)
    return case


def frozen_catalog(root: Path) -> tuple[list[dict], dict]:
    freeze_path = root / 'configs/v3-design/selection-freeze.json'
    if verify_freeze(root) != digest(freeze_path):
        raise ValueError('design freeze differs')
    freeze = read(freeze_path)
    if freeze['selected_candidate'] != 0 or len(freeze['cases']) != 56:
        raise ValueError('expected the 56 selected public cases')
    if digest(root / 'src/sn/planner_v3_single_r3.py') != PLANNER_HASH:
        raise ValueError('R3 planner differs')
    if digest(root / 'results/v3_design_false_notice_r2/result.json') != DESIGN_HASH:
        raise ValueError('design result differs')
    if digest(root / 'results/v3_design_false_notice_r2/verification.json') != DESIGN_VERIFY_HASH:
        raise ValueError('design verification differs')
    baseline = read(root / 'results/v3_design_false_notice_r2/result.json')
    for name in ('v3_truthful_calibration_r4', 'v3_design_false_notice_r2'):
        result = read(root / f'results/{name}/result.json')
        checked = read(root / f'results/{name}/verification.json')
        if (result['status'] != 'completed' or result['failures']
                or checked['status'] != 'verified'
                or checked['result_sha256'] != digest(root / f'results/{name}/result.json')
                or result['progress_sha256'] != digest(root / f'results/{name}/progress.jsonl')
                or checked['progress_sha256'] != result['progress_sha256']):
            raise ValueError('unverified endpoint evidence: ' + name)
    for item in freeze['cases']:
        case = load_case(root, item)
        if (case.contract.demand_rule != 'lost_sales' or case.network.horizon != 20
                or case.contract.liability_multiplier != 1
                or case.contract.fixed_contradiction_cost != 0):
            raise ValueError('baseline case conventions differ')
    return freeze['cases'], baseline


def select_profile(catalog: list[dict]) -> list[dict]:
    """First row in frozen-manifest order in each family-size stratum."""
    first = {}
    for item in catalog:
        first.setdefault((item['family'], item['size']), item)
    if len(first) != 10:
        raise ValueError('expected ten family-size strata')
    return list(first.values())


def expected_manifest(root: Path, mode: str) -> dict:
    manifest_path(root, mode)
    catalog, _ = frozen_catalog(root)
    selected = select_profile(catalog) if mode == 'profile' else catalog
    prerequisite = {}
    caps = dict(PROFILE_CAPS)
    if mode == 'study':
        profile_path = manifest_path(root, 'profile')
        profile = read(profile_path)
        result = read(root / paths('profile')['result'])
        verified = read(root / paths('profile')['verification'])
        current = {name: digest(root / name) for name in SOURCES + EVIDENCE}
        if (profile != expected_manifest(root, 'profile') or profile['input_sha256'] != current
                or result['status'] != 'completed' or result['completed_paths'] != 30 or result['failures']
                or result['execution_manifest_sha256'] != digest(profile_path)
                or verified['status'] != 'verified'
                or verified['result_sha256'] != digest(root / paths('profile')['result'])):
            raise ValueError('complete independently verified profile of these sources required')
        caps.update(paths=168, maximum_mip_phases=3360,
                    wall_seconds=max(600, 60 * math.ceil(2 * result['elapsed_seconds'] * 168 / 30 / 60)),
                    peak_rss_mib=max(512, 64 * math.ceil(2 * result['peak_rss_mib'] / 64)),
                    saved_bytes=268435456)
        prerequisite = {str(p.relative_to(root)): digest(p) for p in (
            profile_path, root / paths('profile')['result'], root / paths('profile')['verification'])}
    tasks = [{**item, 'alpha': alpha, 'path_id': item['case_id'] + f'|alpha={alpha:.2f}'}
             for item in selected for alpha in ALPHAS]
    return {
        'schema': 1, 'kind': 'v3_discounted_belief_' + mode + '_r1', 'mode': mode,
        'status': 'proposal_unrun_pending_exact_owner_hash',
        'environment': {'python': platform.python_version(), 'system': platform.system(),
                        'machine': platform.machine(), 'scipy': version('scipy'), 'numpy': version('numpy'),
                        'highs': '.'.join(str(getattr(highs_core, 'HIGHS_VERSION_' + k))
                                          for k in ('MAJOR', 'MINOR', 'PATCH'))},
        'input_sha256': {name: digest(root / name) for name in SOURCES + EVIDENCE},
        'catalog': catalog, 'tasks': tasks, 'alpha_levels': list(ALPHAS),
        'profile_selection': 'first row in frozen-manifest order per family-size stratum',
        'planner': {'sha256': PLANNER_HASH, 'presolve': False, 'threads': 1,
                    'actual_type': 'long', 'schedule': 'minor', 'demand_multiplier': 1.0,
                    'prior': 'P(short)=alpha; P(long)=1-alpha',
                    'revelation': 'actual long with probability one from declared revelation period'},
        'resource_caps': caps, 'prerequisite_sha256': prerequisite,
        'receipt_decision': 'owner_approved_v3_discounted_belief_' + mode + '_r1',
        'paths': paths(mode), 'failure_rule': 'stop at first failure or cap; retain full evidence; zero retries',
        'scope': {'public_design_only': True, 'confirmation_access': False,
                  'supplier_or_h1_quantities_saved': mode == 'study',
                  'new_hypothesis_tests': False, 'm5_paused': True, 'llm_paused': True},
        'report': {'all_levels_regardless_of_sign': True,
                   'endpoints': {'0': 'saved C10', '1': 'saved C11', 'reference': 'saved C00'},
                   'aggregation': 'arithmetic network means, minima, maxima and sign counts; no intervals',
                   'groups': ['all', 'R1-interior', 'R1-boundary', 'R2', 'R3', 'R4']},
    }


def verify_manifest(root: Path, path: Path) -> tuple[dict, str]:
    value = read(path)
    if path.resolve() != manifest_path(root.resolve(), value['mode']):
        raise ValueError('unexpected manifest location')
    if value != expected_manifest(root, value['mode']):
        raise ValueError('manifest differs from reviewed inputs, scope or caps')
    return value, digest(path)
