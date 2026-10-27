"""Publish verified historical releases without modifying the current installer."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / '.github/archive-releases.json').read_text())
REPO = os.environ['GH_REPO']
if REPO != 'MarcosDavid23/Turbo-Hunter':
    raise RuntimeError('Unexpected repository')


def gh(*args, optional=False):
    result = subprocess.run(['gh', *args], check=False, capture_output=True, text=True)
    if result.returncode:
        if optional and 'HTTP 404' in result.stderr:
            return None
        raise RuntimeError(result.stderr or result.stdout or 'GitHub operation failed')
    return result.stdout


def api(path, optional=False):
    text = gh('api', f'repos/{REPO}/{path}', optional=optional)
    return None if text is None else json.loads(text)


def verify_current():
    expected = MANIFEST['current']
    release = api('releases/tags/' + expected['tag'])
    tag = api('git/ref/tags/' + expected['tag'])
    assert release['id'] == expected['id'] and not release['draft']
    assert release['target_commitish'] == expected['commit']
    assert tag['object']['sha'] == expected['commit']
    assert len(release['assets']) == 1
    assert release['assets'][0]['digest'] == 'sha256:' + expected['sha256']
    return release


def verify_archive(item):
    archive = ROOT / item['asset']
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == item['sha256']
    tree = api('git/trees/' + item['commit'] + '?recursive=1')
    assert not tree['truncated']
    blobs = {e['path']: e['sha'] for e in tree['tree'] if e['type'] == 'blob'}
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        files = [n for n in zipped.namelist() if not n.endswith('/')]
        assert len(files) == item['files'] and len(set(files)) == len(files)
        assert len({PurePosixPath(n).parts[0] for n in files}) == 1
        for name in files:
            path = PurePosixPath(name)
            assert not path.is_absolute() and '..' not in path.parts
            relative = str(PurePosixPath(*path.parts[1:]))
            data = zipped.read(name)
            digest = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            assert blobs[relative] == digest, relative
    print('Original ZIP and source verified:', item['version'], flush=True)


def verify_release(item):
    release = api('releases/tags/' + item['tag'])
    tag = api('git/ref/tags/' + item['tag'])
    assert not release['draft'] and not release['prerelease']
    assert tag['object']['type'] == 'commit' and tag['object']['sha'] == item['commit']
    assert len(release['assets']) == 1
    asset = release['assets'][0]
    assert asset['name'] == Path(item['asset']).name
    assert asset['digest'] == 'sha256:' + item['sha256']


verify_current()
for item in MANIFEST['releases']:
    verify_archive(item)

with tempfile.TemporaryDirectory() as temporary:
    for item in MANIFEST['releases']:
        title = 'Turbo Hunter ' + item['version']
        notes = (
            f'## {title}\n\n'
            '**Historical release — kept for reference.**\n\n'
            'For the current version and recent fixes, download '
            '[Turbo Hunter 0.6.0](https://github.com/MarcosDavid23/Turbo-Hunter/releases/tag/v0.6.0).\n\n'
            'The attached ZIP is the original package, preserved without changes. '
            'It does not include fixes from later versions. '
            'Installation instructions are included inside the ZIP.\n\n'
        )
        if item.get('recovered'):
            notes += 'The source snapshot for this tag was recovered from the original ZIP when this historical release was archived.\n\n'
        else:
            notes += 'The source tag points to the original version commit; every packaged file was checked against that source.\n\n'
        notes += f'SHA-256 of the original ZIP:\n`{item["sha256"]}`\n'
        notes_path = Path(temporary) / (item['tag'] + '.md')
        notes_path.write_text(notes, encoding='utf-8')
        existing = api('releases/tags/' + item['tag'], optional=True)
        if existing is None:
            existing_tag = api('git/ref/tags/' + item['tag'], optional=True)
            if existing_tag:
                assert existing_tag['object']['type'] == 'commit'
                assert existing_tag['object']['sha'] == item['commit']
            gh('release', 'create', item['tag'], str(ROOT / item['asset']),
               '--target', item['commit'], '--draft', '--latest=false',
               '--title', title, '--notes-file', str(notes_path))
        elif existing['draft']:
            assert not existing['immutable'] and existing['target_commitish'] == item['commit']
            assert all(a['digest'] == 'sha256:' + item['sha256'] for a in existing['assets'])
            if not existing['assets']:
                gh('release', 'upload', item['tag'], str(ROOT / item['asset']))
        else:
            verify_release(item)
        gh('release', 'edit', item['tag'], '--draft=false', '--latest=false',
           '--title', title, '--notes-file', str(notes_path))
        verify_release(item)
        print('Historical release published:', item['tag'], flush=True)

# Normalize the existing title, keeping its original notes and source tag.
gh('release', 'edit', 'v0.4.1', '--title', 'Turbo Hunter 0.4.1', '--latest=false')
verify_current()
gh('release', 'edit', MANIFEST['current']['tag'], '--latest')
assert api('releases/latest')['id'] == MANIFEST['current']['id']
print('Historical releases verified; 0.6.0 remains the latest release.', flush=True)
