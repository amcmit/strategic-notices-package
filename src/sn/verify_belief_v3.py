"""Independent discounted-belief evidence checks. No optimizer calls.

Shares only bound-input/receipt/loading utilities with the runner. Probability
construction, physical replay, quantity reductions and group reports are checked
here without importing its scenario, totals or aggregation functions.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import math
from pathlib import Path

from .allocation_v3 import canonical
from .belief_v3_common import ALPHAS, digest, load_case, manifest_path, read, receipt, verify_manifest
from .core import Action, Notice, Production, Realization, Shipment
from .core_v3 import initial_state_v3, step_v3
from .handoff_v3_single import MAX_PROJECTION
from .planner_v3_single_r3 import PlanningProblem, Scenario


def numeric_tree(value):
    """Normalize JSON sequences, preserving exact numerical equality and bools."""
    if isinstance(value, bool):
        return ('boolean', value)
    if isinstance(value, dict):
        return {key: numeric_tree(v) for key, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [numeric_tree(v) for v in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError('nonfinite saved value')
    return value


def close_tree(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(close_tree(left[k], right[k]) for k in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(close_tree(a, b) for a, b in zip(left, right))
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if type(left) is int or type(right) is int:
        return left == right
    if isinstance(left, (float, int)) and isinstance(right, (float, int)):
        return math.isfinite(left) and math.isfinite(right) and math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-9)
    return left == right


def action(value):
    if set(value) != {'qualify', 'primary_order', 'spot_order', 'production', 'shipments'} or type(value['qualify']) is not bool:
        raise ValueError('invalid executed-action structure')
    numeric_tree(value)
    return Action(value['qualify'], value['primary_order'], value['spot_order'],
                  tuple(Production(**x) for x in value['production']),
                  tuple(Shipment(**x) for x in value['shipments']))


def certificate(row, *, current=True):
    if row['status'] != 0 or row['optimal'] is not True:
        raise ValueError('uncertified solver result')
    p, d = row['primal'], row['dual']
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (p, d)):
        raise ValueError('missing or nonfinite bound')
    limit = 1e-7 * max(1.0, abs(p), abs(d))
    if abs(p - d) > limit:
        raise ValueError('solver gap outside frozen R3 objective-scaled threshold')
    if current:
        expected = {'phase': 'single', 'requested_relative_gap': 0.0,
            'requested_mip_absolute_gap': 0.0, 'requested_mip_feasibility_tolerance': 1e-8,
            'requested_presolve': False, 'requested_threads': 1, 'requested_random_seed': 0,
            'checked_feasibility_tolerance': 1e-7, 'checked_integrality_tolerance': 1e-8,
            'checked_primal_dual_absolute_tolerance': limit, 'time_limit_seconds': 30}
        for key, value in expected.items():
            if row[key] != value:
                raise ValueError('certificate setting differs: ' + key)
    return abs(p - d), limit


def probabilities(case, period, alpha):
    if period >= case.contract.revelation_period:
        return {'long': 1.0}
    if alpha == 0:
        return {'long': 1.0}
    if alpha == 1:
        return {'short': 1.0}
    return {'short': alpha, 'long': 1 - alpha}


def replay(case, saved, *, alpha=None, baseline_cell=None, time_limit=30):
    if saved['status'] != 'completed' or len(saved['periods']) != case.network.horizon:
        raise ValueError('incomplete path cannot supply a descriptive result')
    state = initial_state_v3(case.network)
    major = baseline_cell == 'C00'
    notice = Notice(case.long_capacity if major else case.short_capacity, 'major' if major else 'minor')
    ledgers, dates = [], []
    for t, row in enumerate(saved['periods']):
        if row['period'] != t:
            raise ValueError('nonsequential period')
        certificate(row['certificate'], current=baseline_cell is None)
        if baseline_cell is None:
            probability = probabilities(case, t, alpha)
            if probability != row['scenario_probabilities']:
                raise ValueError('wrong prior-to-revelation probabilities')
            sc = tuple(Scenario(name, p, case.short_capacity if name == 'short' else case.long_capacity)
                       for name, p in probability.items())
            call = PlanningProblem(case.network, case.contract, notice, state, sc,
                case.long_capacity[t], case.baseline_demand[t], case.baseline_demand,
                time_limit, False, 1)
            if sha256(canonical(asdict(call))).hexdigest() != row['planning_problem_sha256']:
                raise ValueError('planning state or information input differs')
        executed = action(row['action'])
        state, ledger = step_v3(case.network, case.contract, notice, state, executed,
            Realization(case.long_capacity[t], case.baseline_demand[t]),
            revealed_schedule=case.long_capacity if t == case.contract.revelation_period else None)
        if 'ledger' in row:
            if numeric_tree(asdict(ledger)) != numeric_tree(row['ledger']):
                raise ValueError(f'value difference in replayed ledger at period {t}')
        elif sha256(canonical(asdict(ledger))).hexdigest() != row['ledger_sha256']:
            raise ValueError(f'saved truthful ledger hash differs at period {t}')
        if executed.qualify:
            dates.append(t)
        ledgers.append(ledger)
    if sha256(canonical(asdict(state))).hexdigest() != saved['final_state_sha256']:
        raise ValueError('final state differs')
    return {'buyer_cost': math.fsum(x.operating_cost for x in ledgers),
            'backup': math.fsum(x.backup_delivered for x in ledgers),
            'primary': math.fsum(x.primary_delivered for x in ledgers),
            'accepted_shortfall': math.fsum(x.primary_shortfall - x.pre_horizon_order_shortfall for x in ledgers),
            'pre_horizon_shortfall': math.fsum(x.pre_horizon_order_shortfall for x in ledgers),
            'lost_sales': math.fsum(v.quantity for x in ledgers for v in x.lost_sales),
            'spot': math.fsum(x.spot_delivered for x in ledgers), 'qualification_dates': dates}


def quantity_row(task, observed, reference):
    displacement = reference['backup'] - observed['backup']
    deliveries = observed['primary'] - reference['primary']
    missed_commitments = observed['accepted_shortfall'] - reference['accepted_shortfall']
    return {key: task[key] for key in ('case_id', 'regime', 'category', 'family', 'size', 'alpha')} | {
        'totals': observed, 'b': displacement, 'delta_d': deliveries,
        'delta_shortfall': missed_commitments, 'g_3_5': 3 * deliveries - 5 * missed_commitments,
        'buyer_harm': observed['buyer_cost'] - reference['buyer_cost']}


def group_report(rows):
    groups = ['all', 'R1-interior', 'R1-boundary', 'R2', 'R3', 'R4']
    answer = []
    for alpha in (0.0, 0.25, 0.5, 0.75, 1.0):
        for group in groups:
            selected = []
            for row in rows:
                if row['alpha'] != alpha:
                    continue
                name = row['regime']
                if name == 'R1':
                    name += '-interior' if row['category'] == 'interior' else '-boundary'
                if group in ('all', name):
                    selected.append(row)
            if not selected:
                raise ValueError('missing group')
            measures = {}
            for key in ('b', 'delta_d', 'delta_shortfall', 'g_3_5', 'buyer_harm'):
                numbers = [row[key] for row in selected]
                measures[key] = {'mean': math.fsum(numbers) / len(numbers),
                    'minimum': min(numbers), 'maximum': max(numbers), 'positive': sum(x > 0 for x in numbers)}
            answer.append({'alpha': alpha, 'group': group, 'n': len(selected),
                'qualification_selected': sum(len(r['totals']['qualification_dates']) > 0 for r in selected),
                'positive_b_and_delta_d': sum(r['b'] > 0 and r['delta_d'] > 0 for r in selected),
                'quantities': measures})
    return answer


def lines(path):
    with path.open('rb') as stream:
        for raw in stream:
            value = json.loads(raw)
            if raw != canonical(value):
                raise ValueError('noncanonical progress record')
            yield value


def verify(root: Path, path: Path):
    manifest, hashed = verify_manifest(root, path)
    receipt(root, manifest, hashed)
    result_path = root / manifest['paths']['result']
    result = read(result_path)
    progress = root / manifest['paths']['progress']
    if (result['status'] != 'completed' or result['failures']
            or result['execution_manifest_sha256'] != hashed
            or result['progress_sha256'] != digest(progress)
            or result['confirmation_access'] is not False or result['new_hypothesis_tests'] is not False):
        raise ValueError('complete, bound, failure-free evidence required')
    caps = manifest['resource_caps']
    expected_phases = len(manifest['tasks']) * 20
    if (result['completed_paths'] != len(manifest['tasks'])
            or result['attempted_paths'] != len(manifest['tasks'])
            or result['mip_phases'] != expected_phases
            or len(result['phase_seconds']) != expected_phases
            or result['elapsed_seconds'] > caps['wall_seconds']
            or result['peak_rss_mib'] > caps['peak_rss_mib']
            or max(result['phase_seconds']) > caps['per_mip_seconds'] + 0.01):
        raise ValueError('path count or resource account differs')
    if sum(p.stat().st_size for p in result_path.parent.iterdir()) > caps['saved_bytes']:
        raise ValueError('saved-byte cap exceeded')
    profile = manifest['mode'] == 'profile'
    baseline_totals, replayed, diagnostics = {}, [], []
    if not profile:
        selected = {item['case_id'] for item in manifest['catalog']}
        original = {}
        for name in ('v3_truthful_calibration_r4', 'v3_design_false_notice_r2'):
            for row in lines(root / f'results/{name}/progress.jsonl'):
                cid, cell = row['path_id'].split('|')
                if cid in selected and cell in ('long/major', 'C10', 'C11'):
                    original[cid, 'C00' if cell == 'long/major' else cell] = row
        saved_baseline = {r['case_id']: r for r in read(root / 'results/v3_design_false_notice_r2/result.json')['case_rows']}
        for item in manifest['catalog']:
            cid = item['case_id']
            case = load_case(root, item)
            baseline_totals[cid] = {}
            for cell in ('C00', 'C10', 'C11'):
                totals = replay(case, original[cid, cell], baseline_cell=cell)
                if not close_tree(totals, saved_baseline[cid]['cells'][cell]):
                    raise ValueError('independently replayed endpoint totals differ')
                baseline_totals[cid][cell] = totals
    records = list(lines(progress))
    if [r['path_id'] for r in records] != [t['path_id'] for t in manifest['tasks']]:
        raise ValueError('path roster differs')
    for task, saved in zip(manifest['tasks'], records):
        case = load_case(root, task)
        if (saved['status'] != 'completed' or saved['alpha'] != task['alpha']
                or len(saved['periods']) != 20):
            raise ValueError('path metadata differs')
        for t, row in enumerate(saved['periods']):
            if row['period'] != t or row['scenario_probabilities'] != probabilities(case, t, task['alpha']):
                raise ValueError('period or scenario mismatch')
            gap, limit = certificate(row['certificate'])
            if (row['certificate']['scipy_version'] != manifest['environment']['scipy']
                    or row['certificate']['highs_version'] != manifest['environment']['highs']):
                raise ValueError('solver runtime version differs from manifest')
            sizes = [abs(c['projected'] - c['raw']) for c in row['handoff_corrections']]
            if any(x > MAX_PROJECTION for x in sizes):
                raise ValueError('handoff correction exceeds frozen boundary')
            if row['phase_seconds'] > 30.01 or row['peak_rss_mib'] > caps['peak_rss_mib']:
                raise ValueError('per-period cap differs')
            diagnostics.append({'absolute_gap': gap, 'closure_ratio': gap / limit,
                'certificate_bytes': len(canonical(row['certificate'])),
                'corrections': len(sizes), 'max_correction': max(sizes, default=0.0)})
            if profile and set(row) != {'period', 'scenario_probabilities', 'planning_problem_sha256',
                    'certificate', 'phase_seconds', 'peak_rss_mib', 'handoff_corrections'}:
                raise ValueError('profile saved non-engineering fields')
        if not profile:
            totals = replay(case, saved, alpha=task['alpha'])
            replayed.append(quantity_row(task, totals, baseline_totals[task['case_id']]['C00']))
    if profile:
        if result['case_rows'] is not None or result['descriptive_table'] is not None:
            raise ValueError('profile saved strategic output')
    else:
        for task in manifest['catalog']:
            for alpha, cell in ((0.0, 'C10'), (1.0, 'C11')):
                pair = baseline_totals[task['case_id']]
                replayed.append(quantity_row({**task, 'alpha': alpha}, pair[cell], pair['C00']))
        if not close_tree(replayed, result['case_rows']) or not close_tree(group_report(replayed), result['descriptive_table']):
            raise ValueError('independently recomputed descriptive result differs')
    verification = {'status': 'verified', 'mode': manifest['mode'],
        'execution_manifest_sha256': hashed, 'result_sha256': digest(result_path),
        'progress_sha256': digest(progress), 'completed_paths': len(records),
        'checked_certificates': len(diagnostics),
        'maximum_absolute_gap': max(d['absolute_gap'] for d in diagnostics),
        'maximum_closure_ratio': max(d['closure_ratio'] for d in diagnostics),
        'certificate_bytes_total': sum(d['certificate_bytes'] for d in diagnostics),
        'certificate_bytes_maximum': max(d['certificate_bytes'] for d in diagnostics),
        'correction_count': sum(d['corrections'] for d in diagnostics),
        'maximum_correction': max(d['max_correction'] for d in diagnostics),
        'physical_paths_replayed': len(records) + 168 if not profile else 0,
        'new_mip_phases': 0, 'new_hypothesis_tests': False, 'confirmation_access': False}
    with (root / manifest['paths']['verification']).open('xb') as stream:
        stream.write(canonical(verification))
    return verification


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repository-root', required=True, type=Path)
    p.add_argument('--mode', choices=['profile', 'study'], default='profile')
    args = p.parse_args()
    root = args.repository_root.resolve()
    print(json.dumps(verify(root, manifest_path(root, args.mode)), sort_keys=True))


if __name__ == '__main__':
    main()
