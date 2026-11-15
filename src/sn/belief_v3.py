"""Single-use public-design discounted-belief profile/experiment runner.

New paths require a receipt naming the exact manifest; propose/review/preflight
never call a solver. The frozen R3 planner is used without source changes.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import math
from pathlib import Path
import resource
import signal
import sys
import tempfile
from time import monotonic
import traceback

from . import planner_v3_single_r3 as planner
from .allocation_v3 import canonical
from .belief_v3_common import (ALPHAS, digest, expected_manifest, load_case,
                              manifest_path, read, receipt, verify_manifest)
from .core import Notice, Realization
from .core_v3 import initial_state_v3, step_v3


class ResourceStop(RuntimeError):
    pass


def rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024 * 1024) if sys.platform == 'darwin' else value / 1024


def scenarios(case, period: int, alpha: float) -> tuple:
    if not 0 <= period < case.network.horizon or not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError('invalid period or probability')
    if period >= case.contract.revelation_period:
        return (planner.Scenario('long', 1.0, case.long_capacity),)
    return tuple(planner.Scenario(name, probability, schedule)
                 for name, probability, schedule in (
                     ('short', alpha, case.short_capacity), ('long', 1 - alpha, case.long_capacity))
                 if probability > 0)


def problem(case, state, alpha, time_limit):
    t = state.period
    return planner.PlanningProblem(case.network, case.contract,
        Notice(case.short_capacity, 'minor'), state, scenarios(case, t, alpha),
        case.long_capacity[t], case.baseline_demand[t], case.baseline_demand,
        time_limit, False, 1)


def descriptive_row(task: dict, totals: dict, baseline: dict) -> dict:
    truth = baseline['cells']['C00']
    b = truth['backup'] - totals['backup']
    d = totals['primary'] - truth['primary']
    shortfall = totals['accepted_shortfall'] - truth['accepted_shortfall']
    return {**{k: task[k] for k in ('case_id', 'regime', 'category', 'family', 'size', 'alpha')},
            'totals': totals, 'b': b, 'delta_d': d, 'delta_shortfall': shortfall,
            'g_3_5': 3 * d - 5 * shortfall,
            'buyer_harm': totals['buyer_cost'] - truth['buyer_cost']}


def grouped(rows: list[dict]) -> list[dict]:
    result = []
    for alpha in (0.0, *ALPHAS, 1.0):
        at = [r for r in rows if r['alpha'] == alpha]
        groups = {'all': at,
                  'R1-interior': [r for r in at if r['regime'] == 'R1' and r['category'] == 'interior'],
                  'R1-boundary': [r for r in at if r['regime'] == 'R1' and r['category'] != 'interior']}
        groups.update({name: [r for r in at if r['regime'] == name] for name in ('R2', 'R3', 'R4')})
        for name, selected in groups.items():
            if not selected:
                raise ValueError('descriptive table is missing a group')
            result.append({'alpha': alpha, 'group': name, 'n': len(selected),
                'qualification_selected': sum(bool(r['totals']['qualification_dates']) for r in selected),
                'positive_b_and_delta_d': sum(r['b'] > 0 and r['delta_d'] > 0 for r in selected),
                'quantities': {key: {'mean': sum(r[key] for r in selected) / len(selected),
                    'minimum': min(r[key] for r in selected), 'maximum': max(r[key] for r in selected),
                    'positive': sum(r[key] > 0 for r in selected)}
                    for key in ('b', 'delta_d', 'delta_shortfall', 'g_3_5', 'buyer_harm')}})
    return result


def _failure(path_id, period, error):
    row = {'path_id': path_id, 'period': period, 'type': type(error).__name__,
           'full_error_text': str(error), 'traceback': traceback.format_exc()}
    if isinstance(error, planner.PlannerFailure):
        row['solver_certificate'] = asdict(error.certificate)
    return row


def preflight(root: Path, path: Path) -> dict:
    manifest, hashed = verify_manifest(root, path)
    caps = manifest['resource_caps']
    peak = rss_mib()
    if not math.isfinite(peak) or not 0 < peak <= caps['peak_rss_mib']:
        raise ResourceStop('zero-solve preflight RSS exceeds cap')
    for task in manifest['tasks']:
        case = load_case(root, task)
        problem(case, initial_state_v3(case.network), task['alpha'], caps['per_mip_seconds'])
    # Single process: verify the output volume, serialization and memory API.
    with tempfile.TemporaryDirectory(prefix='.belief-preflight-', dir=root / 'results') as d:
        probe = Path(d) / 'probe.json'
        probe.write_bytes(canonical({'manifest': hashed, 'rss_mib': peak}))
        if read(probe)['manifest'] != hashed:
            raise ValueError('zero-solve write/read preflight failed')
    return {'status': 'passed', 'execution_manifest_sha256': hashed, 'mip_phases': 0,
            'tasks_checked': len(manifest['tasks']), 'peak_rss_mib': rss_mib(),
            'attempt_consumed': False}


def run(root: Path, path: Path) -> dict:
    root = root.resolve()
    manifest, hashed = verify_manifest(root, path)
    receipt(root, manifest, hashed)
    output = root / manifest['paths']['output_root']
    if output.exists():
        raise FileExistsError('attempt already exists; zero retries')
    preflight_record = preflight(root, path)
    output.mkdir()
    (root / manifest['paths']['attempt_marker']).write_bytes(canonical({'execution_manifest_sha256': hashed}))
    progress = root / manifest['paths']['progress']
    caps = manifest['resource_caps']
    profile = manifest['mode'] == 'profile'
    baseline = {} if profile else {r['case_id']: r for r in read(
        root / 'results/v3_design_false_notice_r2/result.json')['case_rows']}
    started = monotonic()
    phases = 0
    elapsed_phases = []
    failures, summaries = [], []
    completed = attempted = 0
    active_id, period = '<runner>', None
    original = planner._solve

    def cap_check():
        if monotonic() - started > caps['wall_seconds']:
            raise ResourceStop('global wall cap reached')
        if rss_mib() > caps['peak_rss_mib']:
            raise ResourceStop('peak RSS cap reached')

    def measured(model, objective, time_limit, phase, **kwargs):
        nonlocal phases
        cap_check()
        if phases >= caps['maximum_mip_phases']:
            raise ResourceStop('MIP phase cap reached')
        marker = root / manifest['paths']['first_solve_marker']
        if not marker.exists():
            marker.write_bytes(canonical({'execution_manifest_sha256': hashed}))
        phases += 1
        before = monotonic()
        try:
            return original(model, objective, time_limit, phase, **kwargs)
        finally:
            elapsed = monotonic() - before
            elapsed_phases.append(elapsed)
            if elapsed > caps['per_mip_seconds'] + 0.01:
                raise ResourceStop('per-MIP cap reached (10 ms measurement allowance)')
            cap_check()

    def alarm(_signum, _frame):
        raise ResourceStop('global wall timer expired')

    old_alarm = signal.getsignal(signal.SIGALRM)
    planner._solve = measured
    signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, caps['wall_seconds'])
    try:
        with progress.open('xb') as stream:
            for task in manifest['tasks']:
                active_id, period = task['path_id'], None
                attempted += 1
                record = {'path_id': active_id, 'alpha': task['alpha'], 'status': 'running', 'periods': []}
                try:
                    cap_check()
                    case = load_case(root, task)
                    state = initial_state_v3(case.network)
                    totals = dict(buyer_cost=0.0, backup=0.0, primary=0.0, accepted_shortfall=0.0,
                                  pre_horizon_shortfall=0.0, lost_sales=0.0, spot=0.0, qualification_dates=[])
                    for period in range(case.network.horizon):
                        call = problem(case, state, task['alpha'], caps['per_mip_seconds'])
                        choice = planner.plan(call)
                        state, ledger = step_v3(case.network, case.contract, call.notice, state,
                            choice.action, Realization(case.long_capacity[period], case.baseline_demand[period]),
                            revealed_schedule=case.long_capacity if period == case.contract.revelation_period else None)
                        row = {'period': period,
                            'scenario_probabilities': {s.name: s.probability for s in call.scenarios},
                            'planning_problem_sha256': sha256(canonical(asdict(call))).hexdigest(),
                            'certificate': asdict(choice.certificate),
                            'phase_seconds': elapsed_phases[-1], 'peak_rss_mib': rss_mib(),
                            'handoff_corrections': [asdict(c) for c in choice.handoff.corrections]}
                        if not profile:
                            row.update(action=asdict(choice.action), ledger=asdict(ledger))
                            totals['buyer_cost'] += ledger.operating_cost
                            totals['backup'] += ledger.backup_delivered
                            totals['primary'] += ledger.primary_delivered
                            totals['accepted_shortfall'] += ledger.primary_shortfall - ledger.pre_horizon_order_shortfall
                            totals['pre_horizon_shortfall'] += ledger.pre_horizon_order_shortfall
                            totals['lost_sales'] += sum(x.quantity for x in ledger.lost_sales)
                            totals['spot'] += ledger.spot_delivered
                            if choice.action.qualify:
                                totals['qualification_dates'].append(period)
                        record['periods'].append(row)
                        cap_check()
                    record['status'] = 'completed'
                    if not profile:
                        record['final_state_sha256'] = sha256(canonical(asdict(state))).hexdigest()
                        summaries.append(descriptive_row(task, totals, baseline[task['case_id']]))
                    completed += 1
                except Exception as error:
                    record['status'] = 'failed'
                    record['failure'] = _failure(active_id, period, error)
                    failures.append(record['failure'])
                encoded = canonical(record)
                # Reserve 256 KiB for a stopped result and complete failure text.
                if stream.tell() + len(encoded) > caps['saved_bytes'] - 262144:
                    raise ResourceStop('progress-byte cap reached; final path record omitted')
                stream.write(encoded)
                stream.flush()
                if record['status'] == 'failed':
                    break
    except Exception as error:
        failures.append(_failure(active_id, period, error))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_alarm)
        planner._solve = original
    complete = completed == caps['paths'] and not failures
    if complete and not profile:
        for task in manifest['catalog']:
            for alpha, cell in ((0.0, 'C10'), (1.0, 'C11')):
                summaries.append(descriptive_row({**task, 'alpha': alpha},
                    baseline[task['case_id']]['cells'][cell], baseline[task['case_id']]))
    table = grouped(summaries) if complete and not profile else None
    try:
        cap_check()
    except Exception as error:
        failures.append(_failure('<finalization>', None, error))
        complete = False
    result = {'status': 'completed' if complete else 'stopped', 'mode': manifest['mode'],
        'execution_manifest_sha256': hashed, 'preflight': preflight_record,
        'attempted_paths': attempted, 'completed_paths': completed, 'mip_phases': phases,
        'failures': failures, 'elapsed_seconds': monotonic() - started, 'peak_rss_mib': rss_mib(),
        'phase_seconds': elapsed_phases, 'progress_sha256': digest(progress),
        'case_rows': summaries if not profile else None,
        'descriptive_table': table if complete else None,
        'confirmation_access': False, 'new_hypothesis_tests': False,
        'm5_paused': True, 'llm_paused': True}
    raw = canonical(result)
    if sum(p.stat().st_size for p in output.iterdir()) + len(raw) > caps['saved_bytes']:
        raise ResourceStop('total-output byte cap exceeded; progress retained')
    (root / manifest['paths']['result']).write_bytes(raw)
    return {'status': result['status'], 'result_sha256': sha256(raw).hexdigest(),
            'completed_paths': completed, 'mip_phases': phases}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['propose', 'review', 'preflight', 'run'])
    parser.add_argument('--repository-root', type=Path, required=True)
    parser.add_argument('--mode', choices=['profile', 'study'], default='profile')
    args = parser.parse_args()
    root = args.repository_root.resolve()
    path = manifest_path(root, args.mode)
    if args.command == 'propose':
        with path.open('xb') as stream:
            stream.write(canonical(expected_manifest(root, args.mode)))
        result = {'manifest_sha256': digest(path), 'mip_phases': 0}
    elif args.command == 'review':
        manifest, hashed = verify_manifest(root, path)
        result = {'manifest_sha256': hashed, 'tasks': len(manifest['tasks']), 'mip_phases': 0}
    elif args.command == 'preflight':
        result = preflight(root, path)
    else:
        result = run(root, path)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
