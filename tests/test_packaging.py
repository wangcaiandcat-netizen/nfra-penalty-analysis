"""Platform installers preserve the runtime while excluding rejected wrappers."""
from pathlib import Path
import importlib.util
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('standalone_packaging', ROOT/'tools/package_skill.py')
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)


def test_skillhub_archive_omits_cmd_without_changing_the_shared_files(tmp_path):
    normal = packaging.build('workbuddy', tmp_path)
    platform = packaging.build('skillhub', tmp_path)
    with zipfile.ZipFile(tmp_path/normal['archive']) as workbuddy, zipfile.ZipFile(tmp_path/platform['archive']) as skillhub:
        assert skillhub.testzip() is None
        assert set(skillhub.namelist()) == set(workbuddy.namelist())-{'run.cmd'}
        assert all(Path(name).suffix.lower() != '.cmd' for name in skillhub.namelist())
        assert all(skillhub.read(name) == workbuddy.read(name) for name in skillhub.namelist())
        assert 'SKILL.md' in skillhub.namelist() and 'scripts/run.py' in skillhub.namelist()


def test_skillhub_can_be_rebuilt_from_a_source_without_the_cmd_wrapper(tmp_path, monkeypatch):
    source = tmp_path/'filtered-source'
    source.mkdir()
    for name in ('SKILL.md', 'run.sh'):
        shutil.copyfile(ROOT/name, source/name)
    for name in packaging.DIRECTORIES:
        shutil.copytree(ROOT/name, source/name, ignore=shutil.ignore_patterns('__pycache__'))
    assert not (source/'run.cmd').exists()
    monkeypatch.setattr(packaging, 'ROOT', source)
    result = packaging.build('skillhub', tmp_path/'dist')
    with zipfile.ZipFile(tmp_path/'dist'/result['archive']) as archive:
        assert archive.testzip() is None and 'run.cmd' not in archive.namelist()
        assert 'scripts/runtime_check.py' in archive.namelist()
