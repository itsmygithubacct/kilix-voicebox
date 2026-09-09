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
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sidecar_authority as authority  # noqa: E402
from prepare_sidecar_resources import main as prepare_main  # noqa: E402
from verify_sidecar_provenance import BRIDGE  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
BINARIES = ROOT / 'tauri/src-tauri/binaries'
REQUIRED = ('VOICEBOX_SIDECAR_SOURCE_COMMIT', 'VOICEBOX_SIDECAR_SOURCE_TREE',
            'VOICEBOX_SIDECAR_STAGED_INPUTS', 'VOICEBOX_SIDECAR_PLATFORM')


def current_source_identity():
    """This checkout's clean commit and tree, or (None, None) if unreadable."""
    try:
        commit = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True, check=False)
        tree = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD^{tree}'],
                              capture_output=True, text=True, check=False)
    except OSError:
        return None, None
    if commit.returncode or tree.returncode:
        return None, None
    return commit.stdout.strip(), tree.stdout.strip()


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
    # then mints a single-use capability for the packaging child it launches.
    # That capability -- not an inherited string, and not the build id the tree
    # publishes about itself -- is the authority here, and it is only accepted
    # while the invocation that minted it is still running. The producer is
    # still not invoked a second time for the same package build.
    capability = os.environ.get(authority.VARIABLE)
    if capability:
        commit, tree = current_source_identity()
        if commit is None:
            return refuse('the current source commit and tree could not be read; '
                          'a packaging capability cannot be bound to this build', 2)
        if not options.platform:
            return refuse('a packaging capability was supplied without '
                          'VOICEBOX_SIDECAR_PLATFORM naming the installed sidecar', 2)
        try:
            record = authority.consume(capability, root=ROOT, source_commit=commit,
                                       source_tree=tree, platform=options.platform)
            authority.verify_artifacts(record)
        except authority.AuthorityError as error:
            return refuse('the packaging authority is not usable: ' + str(error), 2)
        print('voicebox: sidecar gate honoured a live capability for build '
              + record['build_id'])
        return 0

    # A standalone Tauri release build reaches the same safe path: it runs the
    # whole bridge itself rather than packaging whatever is already installed.
    # Without explicit inputs it refuses; it never falls back to stale outputs.
    # -B, not the environment: -I implies -E, so PYTHONDONTWRITEBYTECODE cannot
    # reach this child, and a child that imports from tools/ would otherwise
    # write tools/__pycache__ into the checkout it is verifying.
    orchestrator = [sys.executable, '-I', '-B', str(ROOT / 'tools/build_sidecar.py')]
    if options.platform:
        orchestrator += ['--platform', options.platform]
    # Nothing about a spent or foreign capability may leak into the build the
    # gate is about to start.
    environment = dict(os.environ)
    environment.pop(authority.VARIABLE, None)
    status = subprocess.run(orchestrator, cwd=ROOT, check=False,
                            env=environment).returncode
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
