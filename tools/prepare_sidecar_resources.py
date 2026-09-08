#!/usr/bin/env python3
"""Prepare the verified provenance and selected notices as Tauri resources.

Nothing is packaged, compiled or executed here. The provenance is validated
first by the strict candidate-v2 consumer; only then are its bytes and the
complete selected Voice and transcribe licence bytes staged privately, verified
against the digests the provenance binds, and published atomically.

A failed preparation never leaves a stale resource set in place to be packaged:
any earlier complete set is moved intact into a retained directory, so its bytes
survive for inspection while the published path holds nothing a later `tauri
build` could pick up. Tauri's own resource list names these files explicitly, so
a missing set fails the package step as well as the gate.

Exit status: 0 prepared, 2 refused, 3 missing authority.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_sidecar_provenance import (  # noqa: E402
    BRIDGE, MissingAuthority, Refused, load_handoff, read_regular, require,
    sha256_bytes, validate)


RESOURCE_ROOT = Path('tauri/src-tauri/resources')
PUBLISHED = 'sidecar'
RETAINED = '.retained'
RECEIPT = 'RESOURCES.json'
PROVENANCE_NAME = 'voicebox-server.provenance.json'
NOTICES = ('licenses/kilix-transcribe/LICENSE', 'licenses/kilix-voice/LICENSE')
MAX_RESOURCE_BYTES = 8 * 1024 ** 2
MAX_TOTAL_BYTES = 64 * 1024 ** 2


def retain(published, retained_root, reason):
    """Move an earlier complete set aside: preserved, and no longer packageable."""
    if not published.exists():
        return None
    retained_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = retained_root / ('%d-%s' % (time.time_ns(), reason))
    os.rename(published, destination)
    return destination


def prepare(*, provenance_path, binary_path, staged_inputs, manifest_path, lock_path,
            build_id, source_commit, source_tree, resource_root):
    """Validate the record against actual bytes, then publish its resources."""
    binary = read_regular(binary_path, 'the sidecar binary', limit=1024 ** 3)
    provenance = read_regular(provenance_path, 'the provenance record', limit=16 * 1024 ** 2)
    manifest = read_regular(manifest_path, 'the input manifest', limit=8 * 1024 ** 2)
    lock = read_regular(lock_path, 'the product lock', limit=64 * 1024 ** 2)
    report = validate(provenance_bytes=provenance, binary_bytes_length=len(binary),
                      binary_sha256=sha256_bytes(binary), build_id=build_id,
                      source_commit=source_commit, source_tree=source_tree,
                      manifest_bytes=manifest, uv_lock_sha256=sha256_bytes(lock))
    return publish(report=report, provenance_bytes=provenance, staged_inputs=staged_inputs,
                   resource_root=resource_root)


def publish(*, report, provenance_bytes, staged_inputs, resource_root):
    """Stage, verify and atomically publish the resources a verified build owns."""
    resource_root = Path(resource_root)
    require(not resource_root.is_symlink(), 'the resource root is a symbolic link')
    for parent in resource_root.parents:
        require(not parent.is_symlink(), 'the resource root has a symbolic link ancestor')
    published = resource_root / PUBLISHED
    retained_root = resource_root / RETAINED
    require(not published.is_symlink(), 'the published resource path is a symbolic link')

    staging = None
    try:
        selected = {row['path']: row for row in report['plaintext']}
        staged = Path(staged_inputs)
        require(not staged.is_symlink(), 'the staged input directory is a symbolic link')
        payloads = {PROVENANCE_NAME: provenance_bytes}
        total = len(provenance_bytes)
        for name in NOTICES:
            require(name in selected, 'the provenance does not bind ' + name)
            data = read_regular(staged / name, 'the selected notice ' + name,
                                limit=MAX_RESOURCE_BYTES)
            require(len(data) == selected[name]['bytes']
                    and sha256_bytes(data) == selected[name]['sha256'],
                    'the staged notice bytes differ from the verified provenance: ' + name)
            payloads[name] = data
            total += len(data)
        require(total <= MAX_TOTAL_BYTES, 'the prepared resource set exceeds its byte bound')

        receipt = {
            'schema': 'kilix.voicebox.sidecar-resources/candidate-v1',
            'build_id': report['build_id'],
            'source_commit': report['source_commit'],
            'sidecar_sha256': report['sidecar_sha256'],
            'sidecar_bytes': report['sidecar_bytes'],
            # The receipt describes the resources beside it, not itself.
            'resources': sorted(
                ({'path': name, 'bytes': len(data), 'sha256': sha256_bytes(data)}
                 for name, data in payloads.items()),
                key=lambda row: row['path']),
        }
        payloads[RECEIPT] = (json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode('utf-8')

        resource_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.staging-', dir=resource_root))
        os.chmod(staging, 0o700)
        for name, data in payloads.items():
            destination = staging / name
            require(destination.resolve().is_relative_to(staging.resolve()),
                    'a prepared resource escapes the staging directory: ' + name)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(data)
        prepared = sorted(str(path.relative_to(staging))
                          for path in staging.rglob('*') if path.is_file())
        require(prepared == sorted(payloads), 'the staged resource population is unexpected')
        for name, data in payloads.items():
            check = read_regular(staging / name, 'the staged resource ' + name,
                                 limit=MAX_RESOURCE_BYTES)
            require(check == data, 'a staged resource differs from its verified bytes: ' + name)
        retain(published, retained_root, 'replaced')
        os.rename(staging, published)
        staging = None
    except BaseException:
        # Any failed preparation, at any point, takes the earlier set out of the
        # packaging path while keeping its bytes: nothing this build did not
        # verify can be packaged, and nothing an earlier build produced is lost.
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        retain(published, retained_root, 'failed')
        raise
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--provenance', required=True, type=Path)
    parser.add_argument('--staged-inputs', required=True, type=Path,
                        help='the stager output directory holding the selected notices')
    parser.add_argument('--input-manifest', required=True, type=Path)
    parser.add_argument('--uv-lock', required=True, type=Path)
    parser.add_argument('--resource-root', type=Path, default=None)
    parser.add_argument('--handoff', type=Path)
    parser.add_argument('--build-id')
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--source-tree', required=True)
    options = parser.parse_args(argv)
    root = options.resource_root or (Path(__file__).resolve().parent.parent / RESOURCE_ROOT)
    try:
        if options.handoff is not None:
            handoff = load_handoff(options.handoff)
            build_id = handoff['build_id']
            require(options.build_id in (None, build_id),
                    'the supplied build id is not the handoff build id')
        elif options.build_id:
            build_id = options.build_id
        else:
            raise MissingAuthority(
                'no current-invocation identity was supplied: pass --handoff or --build-id')
        receipt = prepare(provenance_path=options.provenance, binary_path=options.binary,
                          staged_inputs=options.staged_inputs,
                          manifest_path=options.input_manifest, lock_path=options.uv_lock,
                          build_id=build_id, source_commit=options.source_commit,
                          source_tree=options.source_tree, resource_root=root)
    except MissingAuthority as error:
        print('voicebox: sidecar gate has no build authority: ' + str(error), file=sys.stderr)
        print('voicebox: bridge required -- ' + BRIDGE, file=sys.stderr)
        return 3
    except Refused as error:
        print('voicebox: sidecar resource preparation refused: ' + str(error), file=sys.stderr)
        print('voicebox: any earlier resource set was retained and is no longer publishable',
              file=sys.stderr)
        return 2
    print(json.dumps({'status': 'prepared', 'build_id': receipt['build_id'],
                      'resources': len(receipt['resources'])}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
