"""Resolve an owned frozen reference and bind every dependency byte consumed."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile

BUN_SHA256 = '0bb81ea882bce5946a1fc0d26d61880276d81fe22714714b5a2b792bdff8ea7a'
DEPENDENCY_ROOTS = ('node_modules', 'app/node_modules', 'web/node_modules', 'tauri/node_modules')


def population(root):
    rows = []
    for prefix in DEPENDENCY_ROOTS:
        directory = root / prefix
        if directory.is_symlink():
            raise ValueError('linked dependency root refused: ' + prefix)
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise ValueError('invalid dependency root: ' + prefix)
        for parent, directories, files in os.walk(directory, followlinks=False):
            for name in sorted(directories + files):
                path = Path(parent) / name
                info = path.lstat()
                relative = path.relative_to(root).as_posix()
                if stat.S_ISLNK(info.st_mode):
                    # Workspace links are normalized to committed workspace paths.
                    target = path.resolve(strict=True)
                    if not target.is_relative_to(root.resolve()):
                        raise ValueError('external dependency link refused: ' + relative)
                    rows.append({'path': relative, 'kind': 'link',
                                 'target': target.relative_to(root.resolve()).as_posix()})
                elif stat.S_ISREG(info.st_mode):
                    raw = path.read_bytes()
                    rows.append({'path': relative, 'kind': 'file', 'bytes': len(raw),
                                 'sha256': hashlib.sha256(raw).hexdigest(),
                                 'executable': bool(info.st_mode & 0o111)})
                elif not stat.S_ISDIR(info.st_mode):
                    raise ValueError('special dependency member refused: ' + relative)
    return sorted(rows, key=lambda row: row['path'])


def require_population(actual, expected):
    if not expected or actual != expected:
        left = {row['path']: row for row in actual}
        right = {row['path']: row for row in expected}
        differences = sorted(path for path in set(left) | set(right) if left.get(path) != right.get(path))
        raise ValueError('dependency code population differs: ' + ', '.join(differences[:8]))


@contextmanager
def verified_frontend(source, bun, parent):
    if hashlib.sha256(bun.read_bytes()).hexdigest() != BUN_SHA256:
        raise ValueError('exact Bun 1.3.8 binary required')
    if subprocess.check_output([str(bun), '--version']).strip() != b'1.3.8':
        raise ValueError('Bun 1.3.8 required')
    with tempfile.TemporaryDirectory(prefix='frozen-web-', dir=parent) as temporary:
        work = Path(temporary)
        fresh = work / 'source'
        fresh.mkdir(mode=0o700)
        # Copy committed blobs only: ignored source/config/dependencies cannot enter.
        tree = subprocess.check_output(['/usr/bin/git', '-C', str(source), 'ls-tree', '-r', '-z', 'HEAD'])
        for entry in tree.split(b'\0'):
            if not entry:
                continue
            meta, name = entry.split(b'\t', 1)
            mode, kind, object_id = meta.decode().split()
            if kind != 'blob' or mode not in {'100644', '100755'}:
                raise ValueError('unsupported committed frontend input')
            target = fresh / name.decode()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output(['/usr/bin/git', '-C', str(source), 'cat-file', 'blob', object_id]))
            target.chmod(0o700 if mode == '100755' else 0o600)
        for name in ('home', 'cache', 'tmp'):
            (work / name).mkdir(mode=0o700)
        configuration = work / 'bunfig.toml'
        configuration.write_text('')
        environment = {'PATH': str(bun.parent) + ':/usr/bin:/bin', 'HOME': str(work / 'home'),
                       'XDG_CACHE_HOME': str(work / 'cache'), 'TMPDIR': str(work / 'tmp'), 'LC_ALL': 'C.UTF-8'}
        lock = (fresh / 'bun.lock').read_bytes()
        command = [str(bun), 'install', '--frozen-lockfile', '--ignore-scripts', '--no-cache',
                   '--cache-dir', str(work / 'cache'), '--backend', 'copyfile', '--linker', 'hoisted',
                   '--registry', 'https://registry.npmjs.org', '--config', str(configuration)]
        # The pinned Bun's default verifies newly downloaded lock integrity. The
        # cache starts empty; no --no-verify or dependency lifecycle script runs.
        subprocess.run(command, cwd=fresh, env=environment, check=True)
        if (fresh / 'bun.lock').read_bytes() != lock:
            raise ValueError('frozen dependency resolution changed the lock')
        expected = population(fresh)
        if not expected:
            raise ValueError('empty frozen dependency population')
        if any((source / name).exists() or (source / name).is_symlink() for name in DEPENDENCY_ROOTS):
            require_population(population(source), expected)
        binding = {'schema': 'kilix.voicebox.javascript-inputs/v1', 'bun_sha256': BUN_SHA256,
                   'lock_sha256': hashlib.sha256(lock).hexdigest(),
                   'fresh_empty_cache': True, 'frozen_lockfile': True, 'integrity_checks': True,
                   'dependency_lifecycle_scripts': False, 'files_and_links': expected}
        yield fresh, binding, environment
        require_population(population(fresh), expected)
