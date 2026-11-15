"""Independent, solver-free replay of proposed v3 design sensitivities."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from fractions import Fraction
from hashlib import sha256
import json
import math
from pathlib import Path

from .allocation_v3 import canonical
from .analytical_window_v3 import WindowCase, window_result
from .core import Notice, Realization
from .core_v3 import initial_state_v3, step_v3
from .verify_false_notice_v3_r3 import _action, _case_row, _same_summary
from . import sensitivity_v3 as source


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    raw = path.read_bytes()
    data = json.loads(raw)
    if raw != canonical(data):
        raise ValueError(f'noncanonical evidence: {path}')
    return data


def _normalized_ledger_value(value):
    """Compare numeric values exactly while retaining nonnumeric type checks."""
    if type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            raise ValueError('nonfinite saved or replayed ledger number')
        return ('number', Fraction(value))
    if type(value) is bool:
        return ('bool', value)
    if type(value) is str:
        return ('string', value)
    if value is None:
        return ('null',)
    if isinstance(value, dict):
        return ('object', tuple((key, _normalized_ledger_value(item))
                                for key, item in sorted(value.items())))
    if isinstance(value, (tuple, list)):
        return ('array', tuple(_normalized_ledger_value(item) for item in value))
    raise TypeError(f'unsupported ledger value type: {type(value).__name__}')


def _check_loaded_evidence(root: Path, manifest: dict, cases: dict,
                           baseline: dict) -> None:
    """Independently bind the runner-loaded objects to frozen public evidence."""
    evidence = manifest['evidence_sha256']
    for name in ('configs/v3-design/manifest.json',
                 'configs/v3-design/selection-freeze.json',
                 'results/v3_design_false_notice_r2/result.json',
                 'results/v3_design_false_notice_r2/verification.json'):
        if _digest(root / name) != evidence[name]:
            raise ValueError(f'frozen evidence content hash differs: {name}')

    design = _read(root / 'configs/v3-design/manifest.json')
    freeze = _read(root / 'configs/v3-design/selection-freeze.json')
    selected = {item['case_id']: item for item in design['cases']
                if item['candidate_index'] == freeze['selected_candidate']}
    frozen = {item['case_id']: item for item in freeze['cases']}
    if (freeze['selected_candidate'] != 0 or len(selected) != 56
            or len(frozen) != 56 or set(cases) != set(selected) == set(frozen)):
        raise ValueError('loaded public case roster differs from frozen design')
    for case_id, case in cases.items():
        content_hash = sha256(canonical(asdict(case))).hexdigest()
        if (content_hash != selected[case_id]['content_sha256']
                or content_hash != frozen[case_id]['content_sha256']):
            raise ValueError(f'loaded public case content hash differs: {case_id}')

    result_path = root / 'results/v3_design_false_notice_r2/result.json'
    saved = _read(result_path)
    independent_rows = {row['case_id']: row for row in saved['case_rows']}
    if (saved['status'] != 'completed' or len(saved['case_rows']) != 56
            or len(independent_rows) != 56 or set(baseline) != set(independent_rows)
            or _digest(root / 'results/v3_design_false_notice_r2/progress.jsonl')
               != saved['progress_sha256']):
        raise ValueError('loaded baseline roster or saved progress hash differs')
    for case_id, row in baseline.items():
        if (sha256(canonical(row)).digest()
                != sha256(canonical(independent_rows[case_id])).digest()):
            raise ValueError(f'loaded baseline row content differs: {case_id}')
    verification = _read(root / 'results/v3_design_false_notice_r2/verification.json')
    if (verification['status'] != 'verified'
            or verification['result_sha256'] != _digest(result_path)):
        raise ValueError('saved baseline verification hash differs')


def _variant(case, arm: str, level: str):
    """Separate construction from the runner, including the private expedite timing."""
    if arm == 'backlog_catchup':
        contract = replace(case.contract, demand_rule='backlog_catchup',
                           catchup_capacity=.5, backlog_cost=1.0)
        return replace(case, contract=contract)
    if arm == 'broker_spot':
        if case.regime == 'R4':
            raise ValueError('R4 is not replanned')
        network = replace(case.network, spot_site='buyer', spot_product='critical')
        contract = replace(case.contract, spot_capacity=.5, spot_price=6)
        return replace(case, network=network, contract=contract)
    if arm == 'backup_price_parity':
        return replace(case, contract=replace(case.contract, backup_price=1))
    if arm == 'net_shortage_value':
        if level not in {'h1', 'h3', 'h10'}:
            raise ValueError('unknown h level')
        return replace(case, contract=replace(case.contract,
                                              net_shortage_value=int(level[1:])))
    if arm == 'long_type_expedite':
        recovered = [t for t, v in enumerate(case.long_capacity) if v > 0]
        if not recovered or recovered[0] <= case.contract.revelation_period:
            raise ValueError('invalid expedite recovery')
        actual = tuple(1.0 if t >= recovered[0]-1 else 0.0
                       for t in range(case.network.horizon))
        return replace(case, long_capacity=actual)
    raise ValueError('unknown sensitivity arm')


def _replay(original, task: dict, saved: dict) -> tuple:
    if saved['status'] != 'completed' or len(saved['periods']) != 20:
        raise ValueError(f'incomplete sensitivity path: {task["path_id"]}')
    case = _variant(original, task['arm'], task['level'])
    notice = Notice(case.short_capacity if task['cell'] in ('C10','C11')
                    else original.long_capacity,
                    'minor' if task['cell'] in ('C10','C11') else 'major')
    state = initial_state_v3(case.network)
    ledgers = []
    dates = []
    for t, period in enumerate(saved['periods']):
        cert = period['certificate']
        if (period['period'] != t or cert['status'] != 0 or cert['optimal'] is not True
                or not math.isfinite(cert['primal']) or not math.isfinite(cert['dual'])
                or abs(cert['primal']-cert['dual']) >
                1e-7*max(1.0, abs(cert['primal']), abs(cert['dual']))):
            raise ValueError(f'invalid optimal certificate: {task["path_id"]} period {t}')
        action = _action(period['action'])
        state, ledger = step_v3(case.network, case.contract, notice, state,
            action, Realization(case.long_capacity[t], case.baseline_demand[t]),
            revealed_schedule=case.long_capacity if t == case.contract.revelation_period else None)
        if (_normalized_ledger_value(asdict(ledger))
                != _normalized_ledger_value(period['ledger'])):
            raise ValueError(f'ledger replay differs: {task["path_id"]} period {t}')
        if action.qualify:
            dates.append(t)
        ledgers.append(ledger)
    if sha256(canonical(asdict(state))).hexdigest() != saved['final_state_sha256']:
        raise ValueError(f'final state differs: {task["path_id"]}')
    return tuple(ledgers), dates


def _combine_cells(case, cells: dict) -> dict:
    """Independent selected-cell arithmetic; no call into the runner summary."""
    cost = {name: cells[name]['buyer_cost'] for name in source.CELLS}
    b = cells['C00']['backup'] - cells['C11']['backup']
    d = cells['C11']['primary'] - cells['C00']['primary']
    s = cells['C11']['accepted_shortfall'] - cells['C00']['accepted_shortfall']
    return {'case_id': case.case_id, 'regime': case.regime,
            'category': case.category, 'family': case.family, 'size': case.size,
            'cells': cells, 'b': b, 'delta_d': d, 'e': d-b, 's': s,
            'g_3_5': 3*d-5*s, 'buyer_harm': cost['C11']-cost['C00'],
            'contractual': cost['C10']-cost['C00'],
            'belief': cost['C01']-cost['C00'],
            'interaction': cost['C11']-cost['C10']-cost['C01']+cost['C00']}


def _expedite(base: dict, fast: dict) -> dict:
    """Recompute option payoffs independently, including the fixed fee."""
    ordinary = 3.0*base['primary']-5.0*base['accepted_shortfall']
    accelerated = 3.0*fast['primary']-5.0*fast['accepted_shortfall']-3.0
    diff = accelerated-ordinary
    return {'selected': diff > 1e-6, 'base_payoff': ordinary,
            'expedited_net_payoff': accelerated, 'difference': diff,
            'tie_or_near_tie': abs(diff) <= 1e-6}


def _g0(case, factors: dict) -> dict:
    """Use the frozen exact G0 formula with independently assembled factors."""
    model = WindowCase(case.network.horizon, factors['short_duration'],
        factors['long_duration'], factors['qualification_lead'],
        Fraction(factors['inventory_cover']), str(case.contract.primary_price),
        str(case.contract.backup_price), str(case.contract.net_shortage_value),
        Fraction(factors['qualification_fee']), factors['commitment'])
    result = window_result(model)
    return {'predict_truthful_qualification': result.truthful_long.qualification_date is not None,
            'predict_window': result.backup_suppression_window,
            'best_truthful_date': result.truthful_long.qualification_date,
            'single_buyer_no_spot_assumptions_hold':
                case.regime != 'R4' and case.contract.spot_capacity == 0
                and case.contract.demand_rule == 'lost_sales'
                and next(t for t, v in enumerate(case.long_capacity) if v > 0)
                    == factors['long_duration']
                and case.family == 'serial' and case.size == 'small',
            'reference_model': 'G0 unit-demand single-buyer without spot'}


def verify_result(root: Path, manifest_path: Path, result_path: Path) -> dict:
    root = root.resolve()
    manifest, manifest_sha = source.verify_manifest(root, manifest_path)
    source._receipt(root / manifest['paths']['approval_receipt'], source.DECISION, manifest_sha)
    if result_path.resolve() != root / manifest['paths']['result']:
        raise ValueError('unexpected sensitivity result path')
    result = _read(result_path)
    progress_path = root / manifest['paths']['progress']
    if (result['status'] != 'completed' or result['execution_manifest_sha256'] != manifest_sha
            or result['progress_sha256'] != _digest(progress_path)
            or result['completed_paths'] != len(manifest['tasks'])
            or result['mip_phases'] != 20*len(manifest['tasks'])
            or result['failures'] or result['confirmation_case_access'] is not False):
        raise ValueError('incomplete or unbound sensitivity evidence')
    cases, factors = source.selected_cases(root)
    baseline = source._baseline_rows(root)
    _check_loaded_evidence(root, manifest, cases, baseline)
    grouped: dict[tuple[str,str,str], dict] = {}
    count = 0
    with progress_path.open('rb') as stream:
        for task in manifest['tasks']:
            raw = stream.readline()
            if not raw:
                raise ValueError('missing sensitivity progress row')
            saved = json.loads(raw)
            if raw != canonical(saved) or saved['path_id'] != task['path_id'] or any(
                saved[k] != task[k] for k in task):
                raise ValueError('sensitivity progress binding differs')
            case = cases[task['case_id']]
            grouped.setdefault((task['arm'],task['level'],task['case_id']), {})[task['cell']] = (
                _replay(case, task, saved))
            count += 1
        if stream.readline():
            raise ValueError('extra sensitivity progress row')
    rows = {}
    for key, paths in grouped.items():
        case = cases[key[2]]
        rows[key] = _case_row({'case_id': case.case_id, 'regime': case.regime,
            'category': case.category, 'family': case.family, 'size': case.size}, paths)
        rows[key]['terminal_backlog'] = {
            cell: sum(item.quantity for item in ledgers[-1].backlog)
            for cell, (ledgers, _) in paths.items()}
    table, disagreements = [], []
    for arm, level in source.LEVELS:
        entries = []
        for case_id, case in cases.items():
            base = baseline[case_id]
            if arm in source.REPLAN and not (arm == 'broker_spot' and case.regime == 'R4'):
                row = rows[(arm, level, case_id)]
                if arm == 'long_type_expedite':
                    selections = {cell: _expedite(base['cells'][cell], row['cells'][cell])
                                  for cell in source.CELLS}
                    chosen_cells = {cell: (row if selections[cell]['selected'] else base)['cells'][cell]
                                    for cell in source.CELLS}
                    terminal = {cell: (row['terminal_backlog'][cell]
                                       if selections[cell]['selected'] else 0.0)
                                for cell in source.CELLS}
                    row = _combine_cells(case, chosen_cells)
                    row['expedite_choices'] = selections
                    row['terminal_backlog'] = terminal
            else:
                row = dict(base)
                if arm == 'positive_contradiction_fee':
                    row['g_3_5'] = 3.0*row['delta_d']-5.0*row['s']-3.0
                elif arm == 'no_damages':
                    row['g_3_5'] = 3.0*row['delta_d']
            row = dict(row)
            row.setdefault('terminal_backlog', {cell: 0.0 for cell in source.CELLS})
            extra_cost = 0.0
            if arm == 'long_type_expedite':
                extra_cost = 3.0*(int(row['expedite_choices']['C11']['selected'])
                                  - int(row['expedite_choices']['C00']['selected']))
            numerator = 3.0*row['delta_d']-extra_cost
            if arm == 'positive_contradiction_fee':
                numerator -= 3.0
            row['supplier_gain'] = (numerator if arm == 'no_damages'
                                    else numerator-5.0*row['s'])
            if arm == 'no_damages':
                category, value = 'undefined_no_damages', None
            elif arm == 'long_type_expedite':
                category, value = 'undefined_option_may_reselect', None
            elif row['s'] > 1e-8:
                category = 'finite_nonnegative' if numerator > 1e-8 else 'no_positive_range'
                value = numerator/row['s'] if category == 'finite_nonnegative' else None
            elif row['s'] < -1e-8:
                category, value = 'gain_increases_with_penalty', None
            else:
                category = ('invariant_positive' if numerator > 1e-8 else
                            'invariant_negative' if numerator < -1e-8 else 'invariant_zero')
                value = None
            row['critical_penalty'] = {'category': category, 'value': value}
            changed = (_variant(case, arm, level) if arm in source.REPLAN and not (
                arm == 'broker_spot' and case.regime == 'R4') else case)
            window = _g0(changed, factors[case_id])
            observed = bool(row['cells']['C00']['qualification_dates'])
            agreement = window['predict_truthful_qualification'] == observed
            entry = {'case_id': case_id, 'regime': case.regime, 'category': case.category,
                     'family': case.family, 'size': case.size,
                     'arm': arm, 'level': level, 'row': row, 'window': window,
                     'truthful_qualification': observed, 'window_agreement': agreement,
                     'unchanged_reference': arm == 'broker_spot' and case.regime == 'R4'}
            entries.append(entry)
            if not agreement:
                disagreements.append({k: entry[k] for k in ('case_id', 'regime', 'category',
                    'family', 'size', 'arm', 'level', 'window', 'truthful_qualification')})
        fields = ('b', 'delta_d', 'e', 's', 'supplier_gain', 'buyer_harm',
                  'contractual', 'belief', 'interaction')
        summary = {'metrics': {field: {'mean': sum(item['row'][field] for item in entries)/len(entries),
                                      'minimum': min(item['row'][field] for item in entries),
                                      'maximum': max(item['row'][field] for item in entries)}
                               for field in fields},
                   'truthful_qualifiers': sum(item['truthful_qualification'] for item in entries),
                   'critical_penalty_categories': {category: sum(
                       item['row']['critical_penalty']['category'] == category for item in entries)
                       for category in sorted({item['row']['critical_penalty']['category']
                                               for item in entries})}}
        table.append({'arm': arm, 'level': level, 'case_count': len(entries),
                      'window_agreement_count': sum(x['window_agreement'] for x in entries),
                      'window_disagreement_count': sum(not x['window_agreement'] for x in entries),
                      'descriptive_summary': summary,
                      'cases': entries})
    if not _same_summary(table, result['fixed_table']) or not _same_summary(
            disagreements, result['window_disagreements']):
        raise ValueError('independent fixed table or window comparison differs')
    if result['fixed_level_status'] != [
            {'arm': arm, 'level': level,
             'expected_new_paths': sum(task['arm'] == arm and task['level'] == level
                                       for task in manifest['tasks']),
             'completed_new_paths': sum(task['arm'] == arm and task['level'] == level
                                        for task in manifest['tasks']),
             'status': 'complete'} for arm, level in source.LEVELS]:
        raise ValueError('fixed-level completion table differs')
    report = {'status': 'verified', 'manifest_sha256': manifest_sha,
              'result_sha256': _digest(result_path), 'progress_sha256': _digest(progress_path),
              'replayed_paths': count, 'fixed_table_rows': len(table),
              'window_disagreements': len(disagreements),
              'expedite_choice_recomputed_independently': True,
              'confirmation_case_access': False}
    out = root / manifest['paths']['verification']
    if out.exists():
        raise FileExistsError('sensitivity verification already exists')
    out.write_bytes(canonical(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository-root', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--result', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_result(args.repository_root, args.manifest, args.result), sort_keys=True))


if __name__ == '__main__':
    main()
