"""Proposed, receipt-gated v3 descriptive sensitivities on public design cases only.

No execution is authorized by importing this module or writing its manifest.
The owner-sealed confirmation set has a separate future gate and is never read here.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import resource
import signal
import sys
import traceback
from time import monotonic

from .allocation_v3 import canonical
from .analytical_window_v3 import WindowCase, window_result
from .core import Notice, Realization
from .core_v3 import initial_state_v3, step_v3
from .false_notice_v3_r2 import summarize_case
from .freeze_v3_design import verify_freeze
from .networks_v3 import _approved_hash, build_public_case, load_allocation
from . import planner_v3_single_r3 as planner
from .profile_v3 import _receipt

MANIFEST_REL = 'configs/proposed-v3-design-sensitivities-r1.json'
RESULT_REL = 'results/v3_design_sensitivities_r1'
DECISION = 'owner_approved_v3_design_sensitivities_r1'
CELLS = ('C00', 'C10', 'C01', 'C11')
LEVELS = (
    ('baseline', 'h20'),
    ('backlog_catchup', 'catchup_0.5_cost_1'),
    ('broker_spot', 'capacity_0.5_price_6'),
    ('positive_contradiction_fee', 'F3'),
    ('no_damages', 'lambda0'),
    ('long_type_expedite', 'advance_1_cost_3'),
    ('backup_price_parity', 'price_1'),
    ('net_shortage_value', 'h1'),
    ('net_shortage_value', 'h3'),
    ('net_shortage_value', 'h10'),
)
REPLAN = {'backlog_catchup', 'broker_spot', 'long_type_expedite',
          'backup_price_parity', 'net_shortage_value'}
SOURCES = (
    'src/sn/allocation_v3.py', 'src/sn/analytical_window_v3.py',
    'src/sn/core.py', 'src/sn/core_v2.py', 'src/sn/core_v3.py',
    'src/sn/handoff_v3_single.py', 'src/sn/networks_v3.py',
    'src/sn/planner_v3_single_r3.py', 'src/sn/false_notice_v3_r2.py',
    'src/sn/verify_false_notice_v3_r3.py', 'src/sn/sensitivity_v3.py',
    'src/sn/verify_sensitivity_v3.py', 'src/sn/profile_v3.py',
    'src/sn/analysis_v3_frozen.py',
)
EVIDENCE = (
    'configs/proposed-v3-allocation.json',
    'configs/v3-design/selection-freeze.json',
    'configs/v3-design/manifest.json',
    'results/v3_design_false_notice_r2/result.json',
    'results/v3_design_false_notice_r2/verification.json',
    'results/v3_truthful_calibration_r4/result.json',
    'results/v3_confirmation_opening/result.json',
    'results/v3_confirmation_opening/verification.json',
    'docs/PROPOSED_V3_PROTOCOL_RECONCILED.md',
    'docs/V3_SINGLE_SOLVE_R3_PROTOCOL.md',
    'docs/V3_PROTOCOL_R5_CONFIRMATION_POPULATION.md',
    'docs/V3_PROTOCOL_R6_FOUR_CELL_DESIGN.md',
    'docs/V3_PROTOCOL_R7_CONFIRMATION_FAILURE_POLICY.md',
    'docs/V3_DESCRIPTIVE_SENSITIVITY_PROPOSAL.md',
)


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    raw = path.read_bytes()
    value = json.loads(raw)
    if raw != canonical(value):
        raise ValueError(f'noncanonical input: {path}')
    return value


def _rss_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024 * 1024) if sys.platform == 'darwin' else value / 1024


def selected_cases(root: Path) -> tuple[dict, dict]:
    """Reconstruct only the 56 frozen public design cases and factor bundles."""
    if verify_freeze(root) != _digest(root / 'configs/v3-design/selection-freeze.json'):
        raise ValueError('design freeze verification failed')
    freeze = _read(root / 'configs/v3-design/selection-freeze.json')
    allocation = load_allocation(root / 'configs/proposed-v3-allocation.json',
                                 root / 'configs/design/manifest.json',
                                 _approved_hash(root / 'configs/owner-approvals/v3-allocation.json'))
    rows = {x['case_id']: x for x in allocation['candidates'][0]['cases']}
    cases = {}
    for item in freeze['cases']:
        case = build_public_case(rows[item['case_id']])
        if sha256(canonical(asdict(case))).hexdigest() != item['content_sha256']:
            raise ValueError(f'public design case differs: {case.case_id}')
        cases[case.case_id] = case
    if len(cases) != 56:
        raise ValueError('expected all 56 public design cases')
    return cases, rows


def expected_manifest(root: Path) -> dict:
    root = root.resolve()
    cases, _ = selected_cases(root)
    baseline = _read(root / 'results/v3_design_false_notice_r2/result.json')
    verification = _read(root / 'results/v3_design_false_notice_r2/verification.json')
    if (baseline['status'] != 'completed' or len(baseline['case_rows']) != 56
            or verification['status'] != 'verified'):
        raise ValueError('frozen design baseline is incomplete')
    tasks = []
    for arm, level in LEVELS:
        if arm not in REPLAN:
            continue
        for case in cases.values():
            if arm == 'broker_spot' and case.regime == 'R4':
                continue
            for cell in CELLS:
                tasks.append({'arm': arm, 'level': level, 'case_id': case.case_id,
                              'cell': cell, 'path_id': f'{arm}:{level}|{case.case_id}|{cell}'})
    if len(tasks) != 1536 or len({x['path_id'] for x in tasks}) != len(tasks):
        raise ValueError('design sensitivity path roster differs')
    return {
        'schema': 1, 'kind': 'v3_design_descriptive_sensitivity_execution_r1',
        'status': 'proposal_unrun_pending_exact_owner_hash',
        'fixed_table': [{'arm': arm, 'level': level} for arm, level in LEVELS],
        'all_levels_reported_regardless_of_outcome': True,
        'tasks': tasks, 'source_sha256': {x: _digest(root / x) for x in SOURCES},
        'evidence_sha256': {x: _digest(root / x) for x in EVIDENCE},
        'test_sha256': _digest(root / 'tests/test_v3_sensitivity_public.py'),
        'planner': {'source': 'planner_v3_single_r3', 'presolve': False,
                    'threads': 1, 'demand_multiplier': 1.0, 'periods': 20},
        'expedite_choice': {'margin': 3.0, 'penalty': 5.0, 'fixed_cost': 3.0,
                            'tie_tolerance': 1e-6, 'tie_choice': 'do_not_expedite',
                            'per_case_and_cell': True},
        'window_comparator': {'source': 'analytical_window_v3.window_result',
                              'every_case_and_level': True,
                              'single_buyer_no_spot_assumption': True,
                              'compare_to': 'truthful_C00_qualification',
                              'list_every_disagreement': True},
        'resource_caps': {'cases': 56, 'new_paths': 1536,
                          'maximum_mip_phases': 30720, 'per_mip_seconds': 30,
                          'wall_seconds': 1800, 'peak_rss_mib': 768,
                          'saved_bytes': 268435456, 'retries': 0},
        'receipt_decision': DECISION,
        'paths': {'approval_receipt': 'configs/owner-approvals/v3-design-sensitivities-r1.json',
                  'output_root': RESULT_REL, 'attempt_marker': RESULT_REL + '/attempt.started',
                  'progress': RESULT_REL + '/progress.jsonl',
                  'result': RESULT_REL + '/result.json',
                  'verification': RESULT_REL + '/verification.json'},
        'scope': {'design_only': True, 'confirmation_access': False,
                  'future_confirmation_separate_manifest_and_reaudit': True,
                  'frozen_results_unchanged': True, 'new_hypothesis_tests': False,
                  'm5_paused': True, 'llm_paused': True},
    }


def verify_manifest(root: Path, path: Path) -> tuple[dict, str]:
    root, path = root.resolve(), path.resolve()
    if path != root / MANIFEST_REL:
        raise ValueError('unexpected design sensitivity manifest path')
    raw = path.read_bytes()
    value = json.loads(raw)
    if raw != canonical(value) or value != expected_manifest(root):
        raise ValueError('design sensitivity manifest differs from bound inputs')
    return value, sha256(raw).hexdigest()


def altered_case(case, arm: str, level: str):
    """The only declared physical/planner perturbations."""
    if arm == 'backlog_catchup':
        return replace(case, contract=replace(case.contract, demand_rule='backlog_catchup',
                           catchup_capacity=0.5, backlog_cost=1.0))
    if arm == 'broker_spot':
        if case.regime == 'R4':
            raise ValueError('R4 is unchanged baseline reference')
        return replace(case,
                       network=replace(case.network, spot_site='buyer', spot_product='critical'),
                       contract=replace(case.contract, spot_capacity=0.5, spot_price=6.0))
    if arm == 'backup_price_parity':
        return replace(case, contract=replace(case.contract, backup_price=1.0))
    if arm == 'net_shortage_value':
        if level not in ('h1', 'h3', 'h10'):
            raise ValueError('unapproved shortage level')
        return replace(case, contract=replace(case.contract, net_shortage_value=float(level[1:])))
    if arm == 'long_type_expedite':
        recovery = next(t for t, capacity in enumerate(case.long_capacity) if capacity > 0)
        if recovery <= case.contract.revelation_period:
            raise ValueError('expedite must remain after revelation')
        return replace(case, long_capacity=tuple(0.0 if t < recovery - 1 else 1.0
                                                  for t in range(case.network.horizon)))
    raise ValueError('unknown replanned arm')


def window_classification(case, row: dict) -> dict:
    """Exact G0 single-buyer comparator; network substitution is outside it."""
    model = WindowCase(
        horizon=case.network.horizon, short_duration=row['short_duration'],
        long_duration=row['long_duration'], lead=row['qualification_lead'],
        inventory=row['inventory_cover'], primary_price=str(case.contract.primary_price),
        backup_price=str(case.contract.backup_price),
        net_shortage=str(case.contract.net_shortage_value),
        fee=str(row['qualification_fee']), commitment=row['commitment'])
    result = window_result(model)
    return {'predict_truthful_qualification': result.truthful_long.qualification_date is not None,
            'predict_window': result.backup_suppression_window,
            'best_truthful_date': result.truthful_long.qualification_date,
            'single_buyer_no_spot_assumptions_hold':
                case.regime != 'R4' and case.contract.spot_capacity == 0
                and case.contract.demand_rule == 'lost_sales'
                and next(t for t, v in enumerate(case.long_capacity) if v > 0)
                    == row['long_duration']
                and case.family == 'serial' and case.size == 'small',
            'reference_model': 'G0 unit-demand single-buyer without spot'}


def payoff(cell: dict, *, margin=3.0, penalty=5.0, fixed_F=0.0,
           liability=1.0, expedite_cost=0.0, contradicted=False) -> float:
    return (margin * cell['primary'] - liability * penalty * cell['accepted_shortfall']
            - (fixed_F if contradicted else 0.0) - expedite_cost)


def expedite_choice(base: dict, fast: dict) -> dict:
    """Supplier chooses separately in each cell; a 1e-6 tie stays unexpedited."""
    base_gain = payoff(base)
    fast_gain = payoff(fast, expedite_cost=3.0)
    selected = fast_gain > base_gain + 1e-6
    return {'selected': selected, 'base_payoff': base_gain,
            'expedited_net_payoff': fast_gain, 'difference': fast_gain - base_gain,
            'tie_or_near_tie': abs(fast_gain - base_gain) <= 1e-6}


def _failure(path_id: str, period: int | None, error: BaseException) -> dict:
    return {'path_id': path_id, 'period': period, 'type': type(error).__name__,
            'full_error_text': str(error), 'traceback': traceback.format_exc()}


def _alarm(_signum, _frame) -> None:
    raise TimeoutError('sensitivity wall cap reached')


def _baseline_rows(root: Path) -> dict:
    result = _read(root / 'results/v3_design_false_notice_r2/result.json')
    rows = {x['case_id']: x for x in result['case_rows']}
    if len(rows) != 56:
        raise ValueError('frozen design baseline lacks 56 rows')
    return rows


def summarize_fixed_table(cases: dict, allocation_rows: dict, baseline: dict,
                          new_rows: dict) -> tuple[list[dict], list[dict]]:
    """Produce every declared arm/level, including baseline and ledger-only arms."""
    table, disagreements = [], []
    for arm, level in LEVELS:
        per_case = []
        for case_id, case in cases.items():
            base = baseline[case_id]
            if arm in REPLAN and not (arm == 'broker_spot' and case.regime == 'R4'):
                row = new_rows[(arm, level, case_id)]
                if arm == 'long_type_expedite':
                    chosen = {cell: expedite_choice(base['cells'][cell], row['cells'][cell])
                              for cell in CELLS}
                    cells = {cell: (row if chosen[cell]['selected'] else base)['cells'][cell]
                             for cell in CELLS}
                    terminal = {cell: (row.get('terminal_backlog', {}).get(cell, 0.0)
                                       if chosen[cell]['selected'] else 0.0)
                                for cell in CELLS}
                    row = summarize_cells(case, cells)
                    row['expedite_choices'] = chosen
                    row['terminal_backlog'] = terminal
            else:
                row = dict(base)
                if arm == 'positive_contradiction_fee':
                    row['g_3_5'] -= 3.0
                elif arm == 'no_damages':
                    row['g_3_5'] = 3.0 * row['delta_d']
            row = dict(row)
            row.setdefault('terminal_backlog', {cell: 0.0 for cell in CELLS})
            expedite_difference = 0.0
            if arm == 'long_type_expedite':
                expedite_difference = 3.0 * (int(row['expedite_choices']['C11']['selected'])
                                             - int(row['expedite_choices']['C00']['selected']))
            numerator = 3.0 * row['delta_d'] - expedite_difference
            if arm == 'positive_contradiction_fee':
                numerator -= 3.0
            row['supplier_gain'] = (numerator if arm == 'no_damages'
                                    else numerator - 5.0 * row['s'])
            row['critical_penalty'] = critical_penalty_category(
                numerator, row['s'], arm)
            variant = altered_case(case, arm, level) if arm in REPLAN and not (
                arm == 'broker_spot' and case.regime == 'R4') else case
            window = window_classification(variant, allocation_rows[case_id])
            observed = bool(row['cells']['C00']['qualification_dates'])
            agreement = window['predict_truthful_qualification'] == observed
            info = {'case_id': case_id, 'regime': case.regime,
                    'category': case.category, 'family': case.family, 'size': case.size,
                    'arm': arm, 'level': level, 'row': row, 'window': window,
                    'truthful_qualification': observed, 'window_agreement': agreement,
                    'unchanged_reference': arm == 'broker_spot' and case.regime == 'R4'}
            per_case.append(info)
            if not agreement:
                disagreements.append({k: info[k] for k in ('case_id', 'regime', 'category',
                    'family', 'size', 'arm', 'level', 'window', 'truthful_qualification')})
        table.append({'arm': arm, 'level': level, 'case_count': len(per_case),
                      'window_agreement_count': sum(x['window_agreement'] for x in per_case),
                      'window_disagreement_count': sum(not x['window_agreement'] for x in per_case),
                      'descriptive_summary': descriptive_summary(per_case),
                      'cases': per_case})
    return table, disagreements


def critical_penalty_category(numerator: float, shortfall: float, arm: str) -> dict:
    if arm == 'no_damages':
        return {'category': 'undefined_no_damages', 'value': None}
    if arm == 'long_type_expedite':
        return {'category': 'undefined_option_may_reselect', 'value': None}
    if shortfall > 1e-8:
        return ({'category': 'finite_nonnegative', 'value': numerator / shortfall}
                if numerator > 1e-8 else
                {'category': 'no_positive_range', 'value': None})
    if shortfall < -1e-8:
        return {'category': 'gain_increases_with_penalty', 'value': None}
    return {'category': ('invariant_positive' if numerator > 1e-8 else
                         'invariant_negative' if numerator < -1e-8 else
                         'invariant_zero'), 'value': None}


def descriptive_summary(entries: list[dict]) -> dict:
    fields = ('b', 'delta_d', 'e', 's', 'supplier_gain', 'buyer_harm',
              'contractual', 'belief', 'interaction')
    values = {name: [item['row'][name] for item in entries] for name in fields}
    return {'metrics': {name: {'mean': sum(v) / len(v), 'minimum': min(v),
                               'maximum': max(v)} for name, v in values.items()},
            'truthful_qualifiers': sum(item['truthful_qualification'] for item in entries),
            'critical_penalty_categories': {category: sum(
                item['row']['critical_penalty']['category'] == category for item in entries)
                for category in sorted({item['row']['critical_penalty']['category']
                                        for item in entries})}}


def summarize_cells(case, cells: dict) -> dict:
    """Recompute four-cell identities from selected cell summaries."""
    c = {name: cells[name]['buyer_cost'] for name in CELLS}
    b = cells['C00']['backup'] - cells['C11']['backup']
    d = cells['C11']['primary'] - cells['C00']['primary']
    s = cells['C11']['accepted_shortfall'] - cells['C00']['accepted_shortfall']
    return {'case_id': case.case_id, 'regime': case.regime, 'category': case.category,
            'family': case.family, 'size': case.size, 'cells': cells, 'b': b,
            'delta_d': d, 'e': d-b, 's': s, 'g_3_5': 3*d-5*s,
            'buyer_harm': c['C11']-c['C00'], 'contractual': c['C10']-c['C00'],
            'belief': c['C01']-c['C00'],
            'interaction': c['C11']-c['C10']-c['C01']+c['C00']}


def run(root: Path, manifest_path: Path) -> dict:
    """One design-only attempt; receipt must name exact owner-approved hash."""
    root = root.resolve()
    manifest, manifest_sha = verify_manifest(root, manifest_path)
    _receipt(root / manifest['paths']['approval_receipt'], DECISION, manifest_sha)
    output = root / RESULT_REL
    if output.exists():
        raise FileExistsError('sensitivity attempt exists; zero retries')
    cases, allocation_rows = selected_cases(root)
    baseline = _baseline_rows(root)
    output.mkdir(parents=True)
    (output / 'attempt.started').write_bytes(canonical({'manifest_sha256': manifest_sha}))
    started = monotonic()
    caps = manifest['resource_caps']
    phase_times, failures = [], []
    phases = completed = 0
    paths: dict[tuple[str, str, str], dict] = {}
    original_solve = planner._solve

    def measured_solve(model, objective, time_limit, phase, **kwargs):
        nonlocal phases
        if phases >= caps['maximum_mip_phases']:
            raise TimeoutError('sensitivity MIP cap reached')
        phases += 1
        began = monotonic()
        try:
            return original_solve(model, objective, time_limit, phase, **kwargs)
        finally:
            phase_times.append(monotonic() - began)
            if phase_times[-1] > caps['per_mip_seconds'] + 0.01:
                raise TimeoutError('sensitivity per-MIP cap reached')
            if _rss_mib() > caps['peak_rss_mib']:
                raise MemoryError('sensitivity RSS cap reached')
            if monotonic() - started > caps['wall_seconds']:
                raise TimeoutError('sensitivity wall cap reached')

    planner._solve = measured_solve
    old_alarm = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, caps['wall_seconds'])
    try:
        with (output / 'progress.jsonl').open('xb') as stream:
            for task in manifest['tasks']:
                period = None
                record = {'path_id': task['path_id'], **task, 'status': 'running', 'periods': []}
                try:
                    case = altered_case(cases[task['case_id']], task['arm'], task['level'])
                    cell = task['cell']
                    notice = Notice(case.short_capacity if cell in ('C10', 'C11')
                                    else cases[case.case_id].long_capacity,
                                    'minor' if cell in ('C10', 'C11') else 'major')
                    state = initial_state_v3(case.network)
                    ledgers = []
                    for period in range(case.network.horizon):
                        # Expedite stays private until the unchanged revelation date.
                        believed = (case.long_capacity if period >= case.contract.revelation_period
                                    else (cases[case.case_id].long_capacity if cell in ('C00','C10')
                                          else case.short_capacity))
                        scenario = planner.Scenario('long' if period >= case.contract.revelation_period
                                                    or cell in ('C00','C10') else 'short', 1.0, believed)
                        problem = planner.PlanningProblem(case.network, case.contract, notice, state,
                            (scenario,), case.long_capacity[period], case.baseline_demand[period],
                            case.baseline_demand, caps['per_mip_seconds'], False, 1)
                        choice = planner.plan(problem)
                        state, ledger = step_v3(case.network, case.contract, notice, state,
                            choice.action, Realization(case.long_capacity[period],
                            case.baseline_demand[period]),
                            revealed_schedule=case.long_capacity if period == case.contract.revelation_period
                            else None)
                        ledgers.append(ledger)
                        cert = choice.certificate
                        record['periods'].append({'period': period, 'action': asdict(choice.action),
                            'ledger': asdict(ledger),
                            'certificate': {'status': cert.status, 'optimal': cert.optimal,
                                'primal': cert.primal, 'dual': cert.dual,
                                'relative_gap': cert.relative_gap, 'message': cert.message},
                            'handoff_corrections': [asdict(x) for x in choice.handoff.corrections]})
                    record['status'] = 'completed'
                    record['final_state_sha256'] = sha256(canonical(asdict(state))).hexdigest()
                    paths.setdefault((task['arm'],task['level'],case.case_id), {})[cell] = (
                        tuple(ledgers), [p['period'] for p in record['periods'] if p['action']['qualify']])
                    completed += 1
                except Exception as error:
                    record['status'] = 'failed'
                    record['failure'] = _failure(task['path_id'], period, error)
                    failures.append(record['failure'])
                stream.write(canonical(record))
                stream.flush()
                if record['status'] == 'failed':
                    break
                if stream.tell() > caps['saved_bytes']:
                    failures.append({'path_id': task['path_id'], 'period': period,
                        'type': 'ResourceStop', 'full_error_text': 'saved byte cap reached',
                        'traceback': ''})
                    break
    except Exception as error:
        failures.append(_failure('<runner>', None, error))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_alarm)
        planner._solve = original_solve
    new_rows = {}
    if not failures and completed == len(manifest['tasks']):
        for key, pathset in paths.items():
            arm, level, case_id = key
            case = cases[case_id]
            new_rows[key] = summarize_case({'case_id': case_id, 'regime': case.regime,
                'category': case.category, 'family': case.family, 'size': case.size}, pathset)
            new_rows[key]['terminal_backlog'] = {
                cell: sum(item.quantity for item in ledgers[-1].backlog)
                for cell, (ledgers, _) in pathset.items()}
        fixed_table, disagreements = summarize_fixed_table(cases, allocation_rows,
                                                              baseline, new_rows)
    else:
        fixed_table, disagreements = None, None
    level_status = []
    completed_ids = {(arm, level, case_id, cell)
                     for (arm, level, case_id), cellset in paths.items() for cell in cellset}
    for arm, level in LEVELS:
        expected = [task for task in manifest['tasks'] if task['arm'] == arm and task['level'] == level]
        count = sum((task['arm'], task['level'], task['case_id'], task['cell'])
                    in completed_ids for task in expected)
        level_status.append({'arm': arm, 'level': level, 'expected_new_paths': len(expected),
                             'completed_new_paths': count,
                             'status': ('complete' if fixed_table is not None else
                                        'incomplete_due_to_stop')})
    progress = output / 'progress.jsonl'
    result = {'status': 'completed' if fixed_table is not None else 'stopped',
              'execution_manifest_sha256': manifest_sha, 'completed_paths': completed,
              'expected_paths': len(manifest['tasks']), 'mip_phases': phases,
              'failures': failures, 'phase_elapsed_seconds_max': max(phase_times, default=0),
              'elapsed_seconds': monotonic()-started, 'peak_rss_mib': _rss_mib(),
              'progress_sha256': _digest(progress), 'fixed_table': fixed_table,
              'fixed_level_status': level_status,
              'window_disagreements': disagreements, 'confirmation_case_access': False,
              'm5_paused': True, 'llm_paused': True}
    raw = canonical(result)
    if len(raw) + progress.stat().st_size > caps['saved_bytes']:
        raise MemoryError('total sensitivity evidence exceeds saved-byte cap')
    (output / 'result.json').write_bytes(raw)
    return {'status': result['status'], 'result_sha256': sha256(raw).hexdigest(),
            'execution_manifest_sha256': manifest_sha}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository-root', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.repository_root, args.manifest), sort_keys=True))


if __name__ == '__main__':
    main()
