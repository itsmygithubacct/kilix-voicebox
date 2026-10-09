#!/usr/bin/env python3
"""Build the offline interpreted web package from explicit clean selected inputs.

This format has its own complete population gate. Tauri packages continue to
require the existing sidecar authority/gate; this tool cannot issue that authority.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parent.parent


def git(source, *args):
    return subprocess.check_output(['/usr/bin/git', '-C', str(source), *args]).strip()


def identity(source, expected=None):
    if git(source, 'status', '--porcelain'):
        raise ValueError('selected source must be clean: ' + source.name)
    commit = git(source, 'rev-parse', 'HEAD').decode()
    if expected and commit != expected:
        raise ValueError('selected input commit differs: ' + source.name)
    return {'commit': commit, 'tree': git(source, 'rev-parse', 'HEAD^{tree}').decode()}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def build(options):
    sources = {'voicebox': identity(ROOT), 'host': identity(options.host_source),
        'voice': identity(options.voice_source),
        'device-lease': identity(options.lease_source, '9ad28c3a34f8b42c49b116ceaa57184bafaffeda'),
        'transcribe-client': identity(options.transcribe_source, '0c61eb4edcdc75e1aca993ef7c8673b3b9aed6a9'),
        'content': identity(options.content_source, 'b0508c0f98e84d84c85f350ebbd6b4192147dd80')}
    if subprocess.check_output([str(options.bun), '--version']).strip() != b'1.3.8':
        raise ValueError('Bun 1.3.8 required')
    locks = {name: digest(ROOT / name) for name in ('bun.lock', 'uv.lock')}
    environment = {k: v for k, v in os.environ.items() if not k.startswith(('VITE_', 'PYTHON', 'BUN_'))}
    subprocess.run([str(options.bun), 'run', '--cwd', str(ROOT / 'web'), 'build'],
        env=environment, check=True)
    if locks != {name: digest(ROOT / name) for name in locks}:
        raise ValueError('dependency lock changed during build')
    producer = module(ROOT / 'kilix_voicebox_backend/build_binary.py', '_locked_sidecar_producer')
    packages = producer._locked_packages(tomllib.loads((ROOT / 'uv.lock').read_text()))
    wheels = {}
    for path in options.wheelhouse.iterdir():
        for name, package in packages.items():
            for row in package.get('wheels', []):
                if row['url'].rsplit('/', 1)[-1] == path.name:
                    if name in wheels or digest(path) != row['hash'].removeprefix('sha256:') or path.stat().st_size != row['size']:
                        raise ValueError('software wheel identity or population differs')
                    wheels[name] = (path, package, row)
    if set(wheels) != set(packages) or len(list(options.wheelhouse.iterdir())) != len(wheels):
        raise ValueError('complete selected locked software wheelhouse required')
    python = options.python_home / 'bin/python3.12'
    if digest(python) != producer.PYTHON_BINARY_SHA256:
        raise ValueError('selected CPython 3.12.8 binary differs')
    version = subprocess.check_output([str(python), '-I', '-S', '-B', '-c', 'import platform;print(platform.python_version())'])
    if version != b'3.12.8\n':
        raise ValueError('CPython 3.12.8 required')
    options.output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='web-delivery-', dir=options.output.parent))
    def put(name, data, executable=False):
        path = stage / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o700 if executable else 0o600)
    try:
        # Product package is taken only from committed blobs, never a dirty tree.
        for line in git(ROOT, 'ls-tree', '-r', sources['voicebox']['commit'], 'kilix_voicebox_backend').splitlines():
            meta, path = line.split(b'\t', 1)
            relative = path.decode()
            if '/tests/' in relative or relative.endswith('build_binary.py'):
                continue
            mode, kind, obj = meta.decode().split()
            if kind != 'blob' or mode not in {'100644', '100755'}:
                raise ValueError('unsupported product source member')
            put('lib/' + relative, subprocess.check_output(['/usr/bin/git', '-C', str(ROOT),
                'cat-file', 'blob', obj]), mode == '100755')
        projection = json.loads((ROOT / '.kilix-import/sidecar-inputs.json').read_text())
        for name, source in [('transcribe', options.transcribe_source)]:
            for row in projection['sources'][name]['members']:
                raw = subprocess.check_output(['/usr/bin/git', '-C', str(source), 'show', 'HEAD:' + row['path']])
                if len(raw) != row['bytes'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
                    raise ValueError('selected helper projection differs')
                target = row['output']
                put(('lib/' + target[7:]) if target.startswith('python/') else target, raw)
        for name, source, prefix, target in (
            ('voice', options.voice_source, 'voicelib', 'lib/voicelib'),
            ('device-lease', options.lease_source, 'components/kilix-device-lease/src/kilix_device_lease', 'shared/kilix_device_lease')):
            for line in git(source, 'ls-tree', '-r', sources[name]['commit'], prefix).splitlines():
                meta, path = line.split(b'\t', 1)
                mode, kind, obj = meta.decode().split()
                if kind != 'blob' or mode not in {'100644', '100755'}:
                    raise ValueError('unsupported shared source member')
                relative = Path(path.decode()).relative_to(prefix).as_posix()
                put(target + '/' + relative, subprocess.check_output(['/usr/bin/git','-C',str(source),
                    'cat-file','blob',obj]), mode == '100755')
            put('licenses/' + name + '/LICENSE', (source / ('LICENSE' if name == 'voice'
                else 'components/kilix-device-lease/LICENSE')).read_bytes())
        put('lib/VERSION', subprocess.check_output(['/usr/bin/git','-C',str(options.voice_source),
            'show',sources['voice']['commit']+':VERSION']))
        # Copy the explicit Python home without external symlink dependencies.
        for path in sorted(options.python_home.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                if path.is_symlink() and not path.resolve().is_relative_to(options.python_home.resolve()):
                    raise ValueError('Python runtime has an external link')
                put('python/' + path.relative_to(options.python_home).as_posix(), path.read_bytes(),
                    bool(path.stat().st_mode & 0o111))
        dependency_rows = []
        for name, (path, package, _) in sorted(wheels.items()):
            rows, record = producer._wheel(path.read_bytes(), name, package['version'])
            for relative, raw, executable in rows:
                if (stage / 'env' / relative).exists():
                    raise ValueError('conflicting dependency payload')
                put('env/' + relative, raw, executable)
            dependency_rows.append({'name': name, 'version': package['version'], 'filename': path.name,
                'sha256': digest(path), 'bytes': path.stat().st_size, 'record_sha256': record})
        for path in sorted((ROOT / 'web/dist').rglob('*')):
            if path.is_file():
                put('ui/' + path.relative_to(ROOT / 'web/dist').as_posix(), path.read_bytes())
        launcher = options.host_source / 'config/kilix_sdk/voicebox.py'
        put('launch.py', launcher.read_bytes())
        put('licenses/voicebox/LICENSE', (ROOT / 'LICENSE').read_bytes())
        javascript = {}
        for path in sorted((ROOT / 'node_modules').rglob('package.json')):
            package = json.loads(path.read_text())
            name, version = package.get('name'), package.get('version')
            if not isinstance(name, str) or not isinstance(version, str):
                continue
            key = name + '@' + version
            if key in javascript:
                continue
            notices = []
            for notice in sorted(path.parent.iterdir()):
                if notice.is_file() and notice.name.lower().startswith(('license', 'licence', 'notice', 'copying')):
                    target = 'licenses/javascript/' + hashlib.sha256(key.encode()).hexdigest()[:16] + '-' + notice.name
                    put(target, notice.read_bytes())
                    notices.append(target)
            javascript[key] = {'name': name, 'version': version,
                'package_json_sha256': digest(path), 'license': package.get('license'), 'notices': notices}
        put('JS-DEPENDENCIES.json', (json.dumps(javascript, indent=2, sort_keys=True) + '\n').encode())
        for name in ('bun.lock', 'uv.lock'):
            put('locks/' + name, (ROOT / name).read_bytes())
        catalog = json.loads((options.content_source / 'src/kilix_content/catalog/plebian.json').read_text())
        put('content-catalog.json', json.dumps(catalog, sort_keys=True).encode())
        native = subprocess.check_output(['/usr/bin/ldd', str(python)], text=True)
        # Loader addresses vary with ASLR and are not a dependency identity.
        # The bundled runtime is relocatable; record its root symbolically.
        native = re.sub(r' \(0x[0-9a-f]+\)', '', native)
        native = native.replace(str(options.python_home.resolve()), '${PYTHON_HOME}')
        probe = ('import sys;sys.path[:0]=sys.argv[1:4];'
            'from voicelib import audio,microphone,device_leases;import kilix_device_lease as canonical;'
            'from pathlib import Path;'
            'assert device_leases.acquire is canonical.acquire;'
            'assert not Path(canonical.__file__).resolve().is_relative_to(Path(audio.__file__).resolve().parent.parent);'
            'import kilix_voicebox_backend.main;print("complete installed backend/shared imports verified")')
        subprocess.run([str(stage / 'python/bin/python3.12'),'-I','-S','-B','-c',probe,
            str(stage/'lib'),str(stage/'env'),str(stage/'shared')],check=True,env=environment)
        rows = []
        for path in sorted(stage.rglob('*')):
            if path.is_file():
                rows.append({'path': path.relative_to(stage).as_posix(), 'bytes': path.stat().st_size,
                    'sha256': digest(path), 'mode': stat.S_IMODE(path.stat().st_mode)})
        value = {'schema': 'kilix.voicebox.web-delivery/v1', 'sources': sources,
            'provider_interfaces': {'content': 'b0508c0f98e84d84c85f350ebbd6b4192147dd80',
                'license': 'b3d5f52c762d967186bd4d7b63430b057b07aa28',
                'qwen': 'c8093798a0a643d3dbacea38979b5d27a46744ce',
                'transcribe': '32e39c4d5b2419c0c698d026d8731b51e4ef3eb1',
                'voice': sources['voice']['commit'],
                'voice_base': 'cba6048cc7d8b6f33a1af5cc3cf84e5c1c34e802',
                'device_lease': sources['device-lease']['commit']},
            'locks': locks, 'dependencies': dependency_rows, 'python_version': '3.12.8',
            'javascript_dependencies': len(javascript),
            'platform': 'linux-x86_64', 'native_prerequisites': native,
            'browser_prerequisite': 'Chromium with sandbox enabled; private X11 and desktop bus',
            'model_payloads': False, 'files': rows}
        put('DELIVERY.json', (json.dumps(value, indent=2, sort_keys=True) + '\n').encode())
        verifier = module(launcher, '_delivery_verifier')
        verifier.verify(stage)
        with options.output.open('xb') as out, gzip.GzipFile(filename='', fileobj=out, mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
                for path in sorted(stage.rglob('*')):
                    if not path.is_file():
                        continue
                    info = tarfile.TarInfo(path.relative_to(stage).as_posix())
                    raw = path.read_bytes()
                    info.size, info.mode = len(raw), stat.S_IMODE(path.stat().st_mode)
                    info.mtime = info.uid = info.gid = 0
                    archive.addfile(info, io.BytesIO(raw))
        options.output.chmod(0o600)
        (Path(str(options.output) + '.sha256')).write_text(digest(options.output) + '\n')
        Path(str(options.output) + '.manifest.json').write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
        if sources['voicebox'] != identity(ROOT) or sources['host'] != identity(options.host_source):
            raise ValueError('source changed during build')
        print(digest(options.output), options.output.name)
    finally:
        shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('host-source', 'voice-source', 'lease-source', 'transcribe-source', 'content-source', 'python-home', 'wheelhouse', 'bun', 'output'):
        parser.add_argument('--' + flag, required=True, type=Path)
    options = parser.parse_args()
    os.umask(0o077)
    build(options)


if __name__ == '__main__':
    main()
