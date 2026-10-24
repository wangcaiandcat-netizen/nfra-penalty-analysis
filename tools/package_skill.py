"""Build the standalone WorkBuddy installer using only the Python standard library."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = ('SKILL.md', 'run.cmd', 'run.sh')
DIRECTORIES = ('agents', 'assets/requests', 'references', 'schemas', 'scripts')
EXTENSIONS = {'.py', '.md', '.json', '.yaml', '.cmd', '.sh'}


def build(target='workbuddy', destination=None):
    if target not in {'workbuddy', 'skillhub'}:
        raise ValueError('Package target must be workbuddy or skillhub')
    version = re.search(r'^\s+version:\s*"(\d{4})\.(\d{2})\.(\d{2})-(r\d+)"\s*$',
                        (ROOT/'SKILL.md').read_text(encoding='utf-8'), re.MULTILINE)
    if version is None:
        raise ValueError('Expected metadata.version in YYYY.MM.DD-rN format')
    year, month, day, revision = version.groups()
    release = f'{year}{month}{day}-{revision}'
    files = {name: ROOT/name for name in ROOT_FILES if target != 'skillhub' or not name.lower().endswith('.cmd')}
    for directory in DIRECTORIES:
        base = ROOT/directory
        if not base.is_dir() or base.is_symlink():
            raise ValueError(f'Missing or unsafe package directory: {directory}')
        for path in base.rglob('*'):
            relative = path.relative_to(ROOT)
            if any(part.startswith('.') or part == '__pycache__' for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError(f'Package symlink is not allowed: {relative}')
            if path.is_file():
                if target == 'skillhub' and path.suffix.lower() == '.cmd':
                    continue
                if path.suffix not in EXTENSIONS or not path.resolve().is_relative_to(ROOT.resolve()):
                    raise ValueError(f'Unexpected package file: {relative}')
                if path.suffix == '.json' and relative.parts[0] not in {'assets', 'schemas'}:
                    raise ValueError(f'Only static requests and schemas may contain JSON: {relative}')
                files[relative.as_posix()] = path
    destination = Path(destination) if destination is not None else ROOT/'dist'
    destination.mkdir(parents=True, exist_ok=True)
    output = destination/f'nfra-penalty-analysis-{target}-{release}.zip'
    entries = []
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(files.items()):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f'Missing or unsafe package file: {name}')
            data = path.read_bytes()
            info = zipfile.ZipInfo(name, date_time=(int(year), int(month), int(day), 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if path.suffix == '.sh' else 0o644) << 16
            archive.writestr(info, data)
            entries.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None or 'SKILL.md' not in archive.namelist():
            raise ValueError('Invalid installer ZIP')
        if target == 'skillhub' and any(Path(name).suffix.lower() == '.cmd' for name in archive.namelist()):
            raise ValueError('SkillHub archives must not contain CMD files')
    report = {'name': 'nfra-penalty-analysis', 'release': release, 'target': target, 'archive': output.name,
              'bytes': output.stat().st_size, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
              'files': entries, 'layout': 'SKILL.md at archive root',
              'default_api_base_url': 'https://deepfin.vip', 'backend_api_contract': '1.2.0'}
    output.with_suffix('.manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    output.with_suffix('.zip.sha256').write_text(report['sha256']+'  '+output.name+'\n', encoding='ascii')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=('workbuddy', 'skillhub'), default='workbuddy',
                        help='skillhub omits CMD launchers; both targets use the same Python client')
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.target), ensure_ascii=False))
