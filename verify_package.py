"""Release integrity checks and an explicitly solver-free test command.

This module never imports study code during integrity verification. Original
scientific verifiers and their execution guards remain unchanged.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import signal
import sys
import tempfile
import time
import traceback

WALL_SECONDS = 900
PEAK_RSS_MIB = 512
MANIFEST = 'PACKAGE_MANIFEST.txt'
INDEX = 'RESULT_HASH_BINDINGS.json'


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_path(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError('invalid relative payload path')
    p = PurePosixPath(name)
    if p.is_absolute() or any(x in ('', '.', '..') for x in name.split('/')):
        raise ValueError('unsafe payload path')
    target = root
    for part in p.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError('symlink in payload path: ' + name)
    return target


def no_duplicate_keys(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError('duplicate JSON key: ' + k)
        result[k] = v
    return result


def read_json(path: Path):
    return json.loads(path.read_text(), object_pairs_hook=no_duplicate_keys)


def read_manifest(root: Path) -> dict[str, str]:
    entries = {}
    for line in safe_path(root, MANIFEST).read_text().splitlines():
        m = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not m:
            raise ValueError('malformed package manifest line')
        h, name = m.groups()
        safe_path(root, name)
        if name in entries or name == MANIFEST:
            raise ValueError('duplicate or self-referential manifest entry')
        entries[name] = h
    if not entries:
        raise ValueError('empty package manifest')
    return entries


def public_case_counts(root: Path, entries: dict[str, str]) -> dict:
    name = 'configs/v3-design/manifest.json'
    if name not in entries:
        raise ValueError('full public design manifest is not distributed')
    manifest = read_json(safe_path(root, name))
    cases = manifest['cases']
    if manifest['case_count'] != 168 or len(cases) != 168:
        raise ValueError('expected 168 generated public cases')
    ids = [r['case_id'] for r in cases]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate public case identifier')
    known = {}
    for row in cases:
        cid = row['case_id']
        if not isinstance(cid, str) or not re.fullmatch(r'v3-c[012]-[a-z0-9_-]+', cid):
            raise ValueError('unsafe public case identifier')
        path = f"configs/v3-design/cases/candidate-{row['candidate_index']}/{cid}.json"
        known[path] = row
    distributed = {n for n in entries if n.startswith('configs/v3-design/cases/')}
    expected = {n for n, r in known.items() if r['candidate_index'] == 0}
    if distributed != expected or len(distributed) != 56:
        raise ValueError('expected exactly the selected 56 public cases')
    for name in sorted(distributed):
        if entries[name] != known[name]['content_sha256']:
            raise ValueError('public case content hash differs: ' + name)
    return {'distributed': 56, 'generated': 168, 'not_distributed': 112}


def verify(root: Path) -> dict:
    root = root.absolute()
    if root.is_symlink():
        raise ValueError('package root is a symlink')
    entries = read_manifest(root)
    actual = set()
    for base, dirs, files in os.walk(root, followlinks=False):
        for n in dirs + files:
            if (Path(base) / n).is_symlink():
                raise ValueError('symlinks are forbidden in a package')
        for n in files:
            actual.add((Path(base) / n).relative_to(root).as_posix())
    if actual != set(entries) | {MANIFEST}:
        raise ValueError('package file set differs: missing=' + repr(sorted(set(entries)-actual))
                         + '; extra=' + repr(sorted(actual-set(entries)-{MANIFEST})))
    for name, expected in entries.items():
        if digest(safe_path(root, name)) != expected:
            raise ValueError('payload hash differs: ' + name)
    if INDEX not in entries:
        raise ValueError('result hash index missing')
    index = read_json(safe_path(root, INDEX))
    checked = []
    for binding in index['bindings']:
        target, record = binding['target'], binding['record']
        if target not in entries or record not in entries:
            raise ValueError('binding is outside the distributed allowlist')
        value = read_json(safe_path(root, record))
        for key in binding['pointer']:
            value = value[key]
        if value != entries[target] or value != binding['sha256']:
            raise ValueError('recorded result hash differs: ' + target)
        checked.append(target)
    # Hash-only limitations are explicit; no absent/private paths are followed.
    unbound = index['hash_only_evidence']
    if any(x not in entries for x in unbound):
        raise ValueError('hash-only index names undistributed material')
    return {'status': 'passed', 'payload_files': len(entries),
            'package_manifest_sha256': digest(root / MANIFEST),
            'recorded_hash_cross_checks': len(checked),
            'unique_cross_checked_files': len(set(checked)),
            'hash_only_evidence_files': len(unbound),
            'hash_only_evidence': unbound,
            'public_cases': public_case_counts(root, entries),
            'scientific_reexecution': False, 'private_access': False}


def environment() -> dict:
    import numpy
    import scipy
    core = importlib.import_module('scipy.optimize._highspy._core')
    bundled = '.'.join(str(getattr(core, 'HIGHS_VERSION_' + x)) for x in ('MAJOR', 'MINOR', 'PATCH'))
    result = {'python': '.'.join(map(str, sys.version_info[:3])),
              'numpy': numpy.__version__, 'scipy': scipy.__version__,
              'scipy_bundled_highs': bundled,
              'version_source': 'scipy.optimize._highspy._core.HIGHS_VERSION_{MAJOR,MINOR,PATCH}'}
    print(json.dumps({'environment': result}, sort_keys=True), flush=True)
    if sys.version_info[:2] != (3, 11) or (numpy.__version__, scipy.__version__, bundled) != ('2.2.6', '1.17.1', '1.12.0'):
        raise RuntimeError('approved environment versions do not match; no substitution allowed')
    return result


class OptimizationForbidden(RuntimeError):
    pass


def blocked_optimization(*args, **kwargs):
    raise OptimizationForbidden('optimization entry point called during solver-free release checks')


def install_optimization_guard() -> list[str]:
    import scipy.optimize as opt
    names = ('milp linprog minimize minimize_scalar basinhopping differential_evolution '
             'dual_annealing direct shgo brute least_squares leastsq root root_scalar '
             'fsolve fmin fmin_bfgs fmin_cg fmin_ncg fmin_l_bfgs_b fmin_slsqp fmin_tnc '
             'fmin_powell fminbound nnls lsq_linear linear_sum_assignment quadratic_assignment').split()
    blocked = []
    for name in names:
        if hasattr(opt, name):
            setattr(opt, name, blocked_optimization)
            blocked.append('scipy.optimize.' + name)
    for module, name in [('scipy.optimize._milp', 'milp'),
                         ('scipy.optimize._linprog', 'linprog'),
                         ('scipy.optimize._milp', '_highs_wrapper'),
                         ('scipy.optimize._linprog_highs', '_highs_wrapper'),
                         ('scipy.optimize._highspy._highs_wrapper', '_highs_wrapper')]:
        m = importlib.import_module(module)
        setattr(m, name, blocked_optimization)
        blocked.append(module + '.' + name)
    for module, clsname in [('scipy.optimize._highspy._core', '_Highs'), ('highspy', 'Highs')]:
        cls = getattr(importlib.import_module(module), clsname)
        for name in ('run', 'presolve', 'postsolve'):
            if hasattr(cls, name):
                setattr(cls, name, blocked_optimization)
                blocked.append(module + '.' + clsname + '.' + name)
    return blocked


def peak_mib() -> float:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / (1024 ** 2 if sys.platform == 'darwin' else 1024)


def run_tests(root: Path) -> int:
    environment()
    start = time.monotonic()
    def cap(*args):
        if time.monotonic() - start > WALL_SECONDS or peak_mib() > PEAK_RSS_MIB:
            raise RuntimeError('release test resource cap exceeded (900 seconds, 512 MiB)')
    def alarm(*args):
        raise RuntimeError('release test wall cap exceeded (900 seconds)')
    signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, WALL_SECONDS)
    blocked = install_optimization_guard()
    print(json.dumps({'optimization_guard': 'active', 'entry_points': blocked,
                      'wall_cap_seconds': WALL_SECONDS, 'peak_rss_cap_mib': PEAK_RSS_MIB}), flush=True)
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / 'paper/margin_repricing_r1'))
    sys.path.insert(0, str(root / 'paper/incentive_map_r1'))
    # Test imports use this same module, including the installed guard.
    sys.modules.setdefault('verify_package', sys.modules[__name__])
    import pytest
    class Limits:
        def pytest_runtest_setup(self, item): cap()
        def pytest_runtest_teardown(self, item, nextitem): cap()
    names = (root / 'SOLVER_FREE_TESTS.txt').read_text().splitlines()
    if len(names) != 8 or len(set(names)) != 8:
        raise ValueError('unexpected solver-free test selection')
    for name in names:
        safe_path(root, name)
    with tempfile.TemporaryDirectory(prefix='sn-release-tests-') as temporary:
        code = pytest.main(['-ra', '-W', 'always', '-p', 'no:cacheprovider',
                            '--basetemp', temporary] + [str(root/n) for n in names], plugins=[Limits()])
    cap()
    signal.setitimer(signal.ITIMER_REAL, 0)
    print(json.dumps({'test_exit_code': int(code), 'wall_seconds': time.monotonic()-start,
                      'peak_rss_mib': peak_mib(), 'retries': 0}), flush=True)
    return int(code)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).absolute().parent)
    parser.add_argument('--environment', action='store_true')
    parser.add_argument('--run-tests', action='store_true')
    args = parser.parse_args()
    if args.environment:
        environment(); return 0
    if args.run_tests:
        return run_tests(args.root.absolute())
    print(json.dumps(verify(args.root), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
