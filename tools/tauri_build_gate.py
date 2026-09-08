#!/usr/bin/env python3
"""Fail-closed sidecar gate for every standalone Tauri release-build route.

Tauri runs this before the frontend build, so a package step is only ever
reached after the installed sidecar, its provenance and the selected notices
have been verified against the identity of the invocation that produced them.

The identity is not guessed. It comes from the collector handoff, or from
explicit VOICEBOX_SIDECAR_* variables, and when neither is present the gate
refuses and prints the exact bridge root has to integrate. It never adopts the
provenance sitting next to the binary, and it never falls back to a set of
resources some earlier build left behind.

Environment:
  VOICEBOX_SIDECAR_HANDOFF        path to the collector handoff (preferred)
  VOICEBOX_SIDECAR_BUILD_ID       this invocation identity, if no handoff exists
  VOICEBOX_SIDECAR_SOURCE_COMMIT  expected clean product commit
  VOICEBOX_SIDECAR_SOURCE_TREE    that commit's tree
  VOICEBOX_SIDECAR_STAGED_INPUTS  stager output directory holding the notices
  VOICEBOX_SIDECAR_PLATFORM       Rust host tuple naming the installed sidecar

Exit status: 0 gate passed, 2 refused, 3 missing authority.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prepare_sidecar_resources import main as prepare_main  # noqa: E402
from verify_sidecar_provenance import BRIDGE  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
BINARIES = ROOT / 'tauri/src-tauri/binaries'
REQUIRED = ('VOICEBOX_SIDECAR_SOURCE_COMMIT', 'VOICEBOX_SIDECAR_SOURCE_TREE',
            'VOICEBOX_SIDECAR_STAGED_INPUTS', 'VOICEBOX_SIDECAR_PLATFORM')


def refuse(message, status):
    print('voicebox: sidecar gate refused before the frontend build: ' + message,
          file=sys.stderr)
    if status == 3:
        print('voicebox: bridge required -- ' + BRIDGE, file=sys.stderr)
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--platform', default=os.environ.get('VOICEBOX_SIDECAR_PLATFORM'))
    options = parser.parse_args(argv)

    # tools/build_sidecar.py runs the producer once and then the consumer, and
    # marks the build it just verified. The receipt beside the published
    # resources must name that same invocation, so this is a check of work
    # already done rather than a discovered file being trusted, and the
    # producer is not invoked a second time for the same package build.
    passed = os.environ.get('VOICEBOX_SIDECAR_GATE_PASSED')
    if passed:
        receipt = ROOT / 'tauri/src-tauri/resources/sidecar/RESOURCES.json'
        if receipt.is_symlink() or not receipt.is_file():
            return refuse('the gate was reported as passed but no resource receipt exists', 2)
        try:
            recorded = json.loads(receipt.read_text(encoding='utf-8'))['build_id']
        except (OSError, ValueError, KeyError):
            return refuse('the resource receipt is unreadable', 2)
        if recorded != passed:
            return refuse('the resource receipt names build %s, not this build %s'
                          % (recorded, passed), 2)
        print('voicebox: sidecar gate already passed for build ' + passed)
        return 0

    # A standalone Tauri release build reaches the same safe path: it runs the
    # whole bridge itself rather than packaging whatever is already installed.
    # Without explicit inputs it refuses; it never falls back to stale outputs.
    orchestrator = [sys.executable, '-I', str(ROOT / 'tools/build_sidecar.py')]
    if options.platform:
        orchestrator += ['--platform', options.platform]
    status = subprocess.run(orchestrator, cwd=ROOT, check=False).returncode
    if status:
        return refuse('the standalone sidecar build did not complete', status)
    print('voicebox: sidecar gate passed; resources prepared for packaging')
    return 0


def legacy_main(argv=None):
    """Retained explicit-artifact path, used by the controls below."""
    options = argparse.Namespace(platform=os.environ.get('VOICEBOX_SIDECAR_PLATFORM'))
    handoff = os.environ.get('VOICEBOX_SIDECAR_HANDOFF')
    build_id = os.environ.get('VOICEBOX_SIDECAR_BUILD_ID')
    if not handoff and not build_id:
        return refuse('no collector handoff and no explicit build id; the gate will not '
                      'adopt an arbitrary provenance record', 3)
    missing = [name for name in REQUIRED
               if name == 'VOICEBOX_SIDECAR_PLATFORM' and not options.platform
               or name != 'VOICEBOX_SIDECAR_PLATFORM' and not os.environ.get(name)]
    if missing:
        return refuse('missing required build authority: ' + ','.join(missing), 3)

    binary = BINARIES / ('voicebox-server-' + options.platform)
    provenance = BINARIES / ('voicebox-server-' + options.platform + '.provenance.json')
    for path, what in ((binary, 'sidecar binary'), (provenance, 'provenance record')):
        if path.is_symlink() or not path.is_file():
            return refuse('the installed ' + what + ' is missing or is not a regular file', 2)

    arguments = ['--binary', str(binary), '--provenance', str(provenance),
                 '--staged-inputs', os.environ['VOICEBOX_SIDECAR_STAGED_INPUTS'],
                 '--input-manifest', str(ROOT / '.kilix-import/sidecar-inputs.json'),
                 '--uv-lock', str(ROOT / 'uv.lock'),
                 '--source-commit', os.environ['VOICEBOX_SIDECAR_SOURCE_COMMIT'],
                 '--source-tree', os.environ['VOICEBOX_SIDECAR_SOURCE_TREE']]
    if handoff:
        arguments += ['--handoff', handoff]
    if build_id:
        arguments += ['--build-id', build_id]
    status = prepare_main(arguments)
    if status:
        return refuse('the sidecar resource preparation did not succeed', status)
    print('voicebox: sidecar gate passed; resources prepared for packaging')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
