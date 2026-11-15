"""Synthetic release-integrity checks; no research or optimization is executed."""
from pathlib import Path
import hashlib
import json
import pytest
import verify_package as v


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + '\n')


def seal(root):
    entries = sorted(p for p in root.rglob('*') if p.is_file() and p.name != v.MANIFEST)
    (root / v.MANIFEST).write_text(''.join(v.digest(p)+'  '+p.relative_to(root).as_posix()+'\n' for p in entries))


def bundle(tmp_path, *, real_style=False):
    root = tmp_path / 'package';root.mkdir()
    cases = []
    for candidate in range(3):
        for i in range(56):
            cid = f'v3-c{candidate}-fixture-{i:03}'
            if real_style and candidate == 0 and i == 0:
                cid = 'v3-c0-r1-interior-layered_dual-small-0'
            raw = (json.dumps({'case_id': cid})+'\n').encode()
            path = root / f'configs/v3-design/cases/candidate-{candidate}/{cid}.json'
            if candidate == 0:
                path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(raw)
            cases.append({'case_id':cid,'candidate_index':candidate,'content_sha256':hashlib.sha256(raw).hexdigest()})
    dump(root/'configs/v3-design/manifest.json', {'case_count':168,'cases':cases})
    dump(root/'result.json', {'status':'synthetic'})
    h=v.digest(root/'result.json')
    dump(root/'verification.json', {'result_sha256':h})
    dump(root/v.INDEX, {'bindings':[{'target':'result.json','record':'verification.json','pointer':['result_sha256'],'sha256':h}], 'hash_only_evidence':[]})
    seal(root)
    return root


def test_complete_synthetic_bundle_and_56_of_168(tmp_path):
    result=v.verify(bundle(tmp_path))
    assert result['status']=='passed'
    assert result['public_cases']=={'distributed':56,'generated':168,'not_distributed':112}
    assert result['recorded_hash_cross_checks']==1
    assert result['private_access'] is False


def test_changed_file_rejected(tmp_path):
    root=bundle(tmp_path);dump(root/'result.json',{'status':'altered'})
    with pytest.raises(ValueError,match='payload hash differs'):v.verify(root)


def test_missing_file_rejected(tmp_path):
    root=bundle(tmp_path);(root/'result.json').unlink()
    with pytest.raises(ValueError,match='file set differs'):v.verify(root)


def test_unlisted_file_rejected(tmp_path):
    root=bundle(tmp_path);(root/'unexpected.txt').write_text('synthetic')
    with pytest.raises(ValueError,match='file set differs'):v.verify(root)


@pytest.mark.parametrize('name',['../outside','/outside','a/../b','a\\b','a//b'])
def test_unsafe_manifest_paths_rejected(tmp_path,name):
    root=bundle(tmp_path)
    (root/v.MANIFEST).write_text('0'*64+'  '+name+'\n')
    with pytest.raises(ValueError,match='payload path'):v.read_manifest(root)


def test_symlink_rejected_without_opening_target(tmp_path):
    root=bundle(tmp_path);(root/'link').symlink_to(tmp_path/'nonexistent')
    with pytest.raises(ValueError,match='symlinks'):v.verify(root)


def test_duplicate_manifest_entry_rejected(tmp_path):
    root=bundle(tmp_path);p=root/v.MANIFEST;p.write_text(p.read_text()+p.read_text().splitlines()[0]+'\n')
    with pytest.raises(ValueError,match='duplicate'):v.verify(root)


def test_recorded_hash_mismatch_rejected_even_with_current_payload_hashes(tmp_path):
    root=bundle(tmp_path);dump(root/'verification.json',{'result_sha256':'0'*64});seal(root)
    with pytest.raises(ValueError,match='recorded result hash differs'):v.verify(root)


def test_result_binding_cannot_load_nondistributed_records(tmp_path):
    root=bundle(tmp_path);index=v.read_json(root/v.INDEX)
    index['bindings'][0]['record']='undistributed.json';dump(root/v.INDEX,index);seal(root)
    with pytest.raises(ValueError,match='outside the distributed allowlist'):v.verify(root)


def test_55_cases_rejected_without_loading_missing_case(tmp_path):
    root=bundle(tmp_path);next((root/'configs/v3-design/cases/candidate-0').glob('*.json')).unlink();seal(root)
    with pytest.raises(ValueError,match='selected 56'):v.verify(root)


def test_case_hash_must_match_full_design_manifest(tmp_path):
    root=bundle(tmp_path);p=root/'configs/v3-design/manifest.json';m=v.read_json(p)
    m['cases'][0]['content_sha256']='0'*64;dump(p,m);seal(root)
    with pytest.raises(ValueError,match='case content hash differs'):v.verify(root)


def test_duplicate_json_key_rejected(tmp_path):
    p=tmp_path/'synthetic.json';p.write_text('{"a":1,"a":2}')
    with pytest.raises(ValueError,match='duplicate JSON key'):v.read_json(p)


def test_guard_installed_and_fail_closed():
    import scipy.optimize as opt
    assert opt.milp is v.blocked_optimization
    assert opt.linprog is v.blocked_optimization
    with pytest.raises(v.OptimizationForbidden):v.blocked_optimization()


def test_real_style_layered_dual_identifier(tmp_path):
    root=bundle(tmp_path,real_style=True)
    path=root/'configs/v3-design/cases/candidate-0/v3-c0-r1-interior-layered_dual-small-0.json'
    assert path.is_file()
    assert v.verify(root)['public_cases']=={'distributed':56,'generated':168,'not_distributed':112}


@pytest.mark.parametrize('case_id',['v3-c0-fixture/escape','v3-c0-fixture..escape'])
def test_path_unsafe_case_identifier_rejected(tmp_path,case_id):
    root=bundle(tmp_path);p=root/'configs/v3-design/manifest.json';m=v.read_json(p)
    m['cases'][0]['case_id']=case_id;dump(p,m);seal(root)
    with pytest.raises(ValueError,match='unsafe public case identifier'):v.verify(root)
