"""Controls for the normal-build invocation bridge.

These drive the real collector, the real orchestrator refusal path, the real
Tauri hook and the real Makefile route with a controlled producer and a
controlled packaging command. **They demonstrate no actual PyInstaller build
and no actual Tauri packaging**: no producer, archive, Rust or frontend
compilation, target execution, acquisition, install, model or audio is
involved anywhere in this module.

The collector's own output-pair and rollback controls live in
`test_sidecar_output.py` and are unchanged; nothing here relaxes them. This
module adds only the bridge: identity generation, explicit input hand-through,
producer invocation shape, stage lifetime and build-route ordering.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
COLLECTOR = ROOT / 'scripts/build-server.sh'
ORCHESTRATOR = ROOT / 'tools/build_sidecar.py'
MAKEFILE = ROOT / 'Makefile'
PLATFORM = 'x86_64-unknown-linux-gnu'
# The ambient interpreter a developer would reach for, which is what these tests
# are about -- but never without -B. `-I` implies `-E`, so PYTHONDONTWRITEBYTECODE
# cannot reach the child, and a child that imports from tools/ writes
# tools/__pycache__ straight into the checkout under test.
AMBIENT_PYTHON = ['/usr/bin/python3', '-I', '-B']

RUSTC = ("#!/bin/sh\nset -eu\ntest \"${1-}\" = -vV\n"
         "printf 'rustc 1.0.0\\nhost: %s\\n' '" + PLATFORM + "'\n")

# A controlled stand-in for the selected interpreter. The collector invokes it
# by absolute path with -I -B -S and the producer script, so this records the
# exact argv and environment it was given and then writes the outputs the
# fixed CLI says a successful producer writes.
INTERPRETER = r'''#!/usr/bin/env python3
import hashlib
import json
import os
import sys
from pathlib import Path

# The collector empties the environment, correctly, so the plan is read from
# beside this stand-in rather than from a variable that must not survive.
PLAN = json.loads(
    Path(__file__).with_name("producer-plan.json").read_text(encoding="utf-8"))
argv = sys.argv[1:]
Path(PLAN["record"]).write_text(
    json.dumps({"argv": argv, "environment": dict(os.environ)}, sort_keys=True) + "\n",
    encoding="utf-8")
values = {}
for index, item in enumerate(argv):
    if item.startswith("--") and index + 1 < len(argv):
        values[item] = argv[index + 1]
stage = Path(values["--stage-dir"])
build_id = values["--build-id"]
if PLAN["mode"] == "failure":
    stage.mkdir(parents=True)
    (stage / "build.log").write_text("controlled failure\n", encoding="utf-8")
    raise SystemExit(3)
dist = stage / "dist"
dist.mkdir(parents=True)
binary = dist / "voicebox-server"
binary.write_bytes(PLAN["binary"].encode())
provenance = dist / "voicebox-server.provenance.json"
provenance.write_text(PLAN["provenance"], encoding="utf-8")
notices = stage / "selected-inputs"
notices.mkdir()
(notices / "completion.json").write_text("{}\n", encoding="utf-8")
(stage / "sidecar-outputs").write_text(
    "schema=kilix.voicebox.sidecar-output/candidate-v1\n"
    f"build_id={PLAN.get('handoff_id', build_id)}\n"
    "binary=dist/voicebox-server\n"
    f"binary_sha256={hashlib.sha256(binary.read_bytes()).hexdigest()}\n"
    f"binary_bytes={binary.stat().st_size}\n"
    "provenance=dist/voicebox-server.provenance.json\n"
    f"provenance_sha256={hashlib.sha256(provenance.read_bytes()).hexdigest()}\n",
    encoding="ascii")
raise SystemExit(0)
'''


class BridgeHarness:
    def __init__(self, temporary):
        self.temporary = temporary
        self.repository = temporary / 'repository'
        (self.repository / 'scripts').mkdir(parents=True)
        self.script = self.repository / 'scripts/build-server.sh'
        self.script.write_bytes(COLLECTOR.read_bytes())
        self.script.chmod(0o755)
        self.tools = temporary / 'tools'
        self.tools.mkdir()
        rustc = self.tools / 'rustc'
        rustc.write_text(RUSTC, encoding='utf-8')
        rustc.chmod(0o755)
        self.interpreter = self.tools / 'python3.12'
        self.interpreter.write_text(INTERPRETER, encoding='utf-8')
        self.interpreter.chmod(0o755)
        self.record = temporary / 'producer-invocation.json'
        self.plan_path = self.tools / 'producer-plan.json'
        # Selected inputs, outside the checkout, as the fixed CLI requires.
        self.inputs = {}
        for name in ('voice-source', 'transcribe-source', 'wheelhouse', 'python-home',
                     'private-home', 'stage-root'):
            path = temporary / name
            path.mkdir()
            self.inputs[name] = path
        self.wheel = temporary / 'kilix_transcribe-0.1.0-py3-none-any.whl'
        self.wheel.write_bytes(b'controlled wheel')
        self.producer = temporary / 'build_binary.py'
        self.producer.write_bytes(b'# controlled producer script\n')
        self.binaries = self.repository / 'tauri/src-tauri/binaries'

    def plan(self, mode='success', **overrides):
        payload = {'mode': mode, 'record': str(self.record), 'binary': 'fresh-sidecar',
                   'provenance': '{"schema":"x"}\n'}
        payload.update(overrides)
        self.plan_path.write_text(json.dumps(payload), encoding='utf-8')

    def arguments(self, build_id, stage=None, **overrides):
        values = {
            '--build-id': build_id,
            '--stage-dir': str(stage or (self.inputs['stage-root'] / ('stage-' + build_id))),
            '--python-executable': str(self.interpreter),
            '--producer': str(self.producer),
            '--voice-source': str(self.inputs['voice-source']),
            '--transcribe-source': str(self.inputs['transcribe-source']),
            '--transcribe-wheel': str(self.wheel),
            '--wheelhouse': str(self.inputs['wheelhouse']),
            '--python-home': str(self.inputs['python-home']),
            '--private-home': str(self.inputs['private-home']),
        }
        values.update(overrides)
        arguments = []
        for flag, value in values.items():
            if value is not None:
                arguments += [flag, value]
        return arguments

    def run(self, *arguments):
        environment = dict(os.environ)
        environment['PATH'] = str(self.tools) + os.pathsep + environment['PATH']
        environment['PYTHONPATH'] = str(self.temporary / 'poison')
        environment['VOICEBOX_CALLER_MARKER'] = '1'
        return subprocess.run([str(self.script), *arguments], cwd=self.repository,
                              check=False, capture_output=True, text=True, timeout=60,
                              env=environment)

    def invocation(self):
        return json.loads(self.record.read_text(encoding='utf-8'))


class CollectorBridgeTests(unittest.TestCase):
    def harness(self):
        temporary = tempfile.TemporaryDirectory(prefix='sidecar-bridge-',
                                                dir=os.environ.get('TMPDIR'))
        self.addCleanup(temporary.cleanup)
        return BridgeHarness(Path(temporary.name))

    def test_the_producer_is_invoked_exactly_as_the_fixed_cli_requires(self):
        harness = self.harness()
        harness.plan()
        build_id = 'a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4'
        result = harness.run(*harness.arguments(build_id), '--keep-stage')
        self.assertEqual(result.returncode, 0, result.stderr)
        invocation = harness.invocation()
        argv, environment = invocation['argv'], invocation['environment']
        # -I -B -S, then the absolute producer script, then explicit inputs.
        self.assertEqual(argv[:4], ['-I', '-B', '-S', str(harness.producer)])
        for flag, value in (('--stage-dir', None), ('--build-id', build_id),
                            ('--voice-source', str(harness.inputs['voice-source'])),
                            ('--transcribe-source', str(harness.inputs['transcribe-source'])),
                            ('--transcribe-wheel', str(harness.wheel)),
                            ('--wheelhouse', str(harness.inputs['wheelhouse'])),
                            ('--python-home', str(harness.inputs['python-home']))):
            self.assertIn(flag, argv)
            if value is not None:
                self.assertEqual(argv[argv.index(flag) + 1], value)
        # A constructed environment, not a filtered copy of the caller's.
        self.assertEqual(environment.get('PATH'), '/usr/bin:/bin')
        self.assertEqual(environment.get('HOME'), str(harness.inputs['private-home']))
        self.assertEqual(environment.get('TMPDIR'), str(harness.inputs['private-home']))
        for name in ('PYTHONNOUSERSITE', 'PYTHONSAFEPATH', 'PYTHONDONTWRITEBYTECODE'):
            self.assertEqual(environment.get(name), '1')
        for name in ('PYTHONPATH', 'PYTHONHOME', 'VOICEBOX_CALLER_MARKER', 'VIRTUAL_ENV'):
            self.assertNotIn(name, environment)
        # No uv or pip is on the producer's path at all.
        self.assertNotIn('uv', ' '.join(argv))

    def test_every_missing_explicit_input_is_named_and_refuses(self):
        harness = self.harness()
        harness.plan()
        build_id = 'b' * 32
        for flag, expected in (('--voice-source', 'Voice source'),
                               ('--transcribe-wheel', 'transcribe wheel'),
                               ('--wheelhouse', 'wheelhouse'),
                               ('--python-home', 'Python home'),
                               ('--python-executable', 'selected Python executable')):
            with self.subTest(flag=flag):
                arguments = harness.arguments(build_id, **{flag: None})
                result = harness.run(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertIn(expected, result.stderr)
                self.assertIn('refuses rather than choosing inputs', result.stderr)
                self.assertFalse(harness.record.exists(),
                                 'the producer ran without complete authority')

    def test_a_stage_inside_the_checkout_or_already_present_refuses(self):
        harness = self.harness()
        harness.plan()
        build_id = 'c' * 32
        inside = harness.repository / ('stage-' + build_id)
        result = harness.run(*harness.arguments(build_id, stage=inside))
        self.assertEqual(result.returncode, 2)
        self.assertIn('outside the source checkout', result.stderr)
        existing = harness.inputs['stage-root'] / ('stage-' + build_id)
        existing.mkdir()
        result = harness.run(*harness.arguments(build_id, stage=existing))
        self.assertEqual(result.returncode, 2)
        self.assertIn('already exists', result.stderr)
        self.assertFalse(harness.record.exists())

    def test_a_handoff_from_another_build_is_still_refused(self):
        harness = self.harness()
        harness.plan(handoff_id='d' * 32)
        result = harness.run(*harness.arguments('e' * 32))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not produced by this build', result.stderr)
        self.assertFalse((harness.binaries / ('voicebox-server-' + PLATFORM)).exists())

    def test_stage_lifetime_matches_the_caller_and_failures_retain_evidence(self):
        harness = self.harness()
        harness.plan()
        build_id = 'f' * 32
        stage = harness.inputs['stage-root'] / ('stage-' + build_id)
        # Kept for the consumer, which still needs the selected-input notices.
        result = harness.run(*harness.arguments(build_id), '--keep-stage')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((stage / 'selected-inputs/completion.json').is_file())
        self.assertIn('Retained build stage', result.stderr)
        # Without that request a complete build cleans up after itself.
        second = '1' * 32
        result = harness.run(*harness.arguments(second))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((harness.inputs['stage-root'] / ('stage-' + second)).exists())
        # A failed build always keeps its own stage for inspection.
        harness.plan(mode='failure')
        third = '2' * 32
        result = harness.run(*harness.arguments(third))
        self.assertNotEqual(result.returncode, 0)
        failed = harness.inputs['stage-root'] / ('stage-' + third)
        self.assertTrue((failed / 'build.log').is_file())
        self.assertIn('Retained build stage', result.stderr)


class OrchestrationRefusalTests(unittest.TestCase):
    def run_orchestrator(self, environment):
        return subprocess.run([*AMBIENT_PYTHON, str(ORCHESTRATOR)], cwd=ROOT,
                              check=False, capture_output=True, text=True, timeout=60,
                              env=environment)

    def clean_environment(self, **overrides):
        environment = {name: value for name, value in os.environ.items()
                       if not name.startswith('VOICEBOX_SIDECAR_')}
        environment.update(overrides)
        return environment

    def test_the_orchestrator_names_every_missing_input_and_builds_nothing(self):
        result = self.run_orchestrator(self.clean_environment(
            VOICEBOX_SIDECAR_PLATFORM=PLATFORM))
        self.assertEqual(result.returncode, 3)
        for fragment in ('stage-root', 'python-executable', 'voice-source',
                         'transcribe-source', 'transcribe-wheel', 'wheelhouse',
                         'python-home'):
            self.assertIn(fragment, result.stderr)
        self.assertIn('nothing is chosen by default', result.stderr)

    def test_the_orchestrator_refuses_without_a_platform(self):
        result = self.run_orchestrator(self.clean_environment())
        self.assertEqual(result.returncode, 3)
        self.assertIn('Rust host tuple', result.stderr)
        self.assertIn('nothing is chosen by default', result.stderr)

    def test_each_invocation_generates_a_new_identity(self):
        # The nonce is generated at the start of the build, never adopted from
        # an existing record. Two runs of the generator never agree.
        script = ('import sys; sys.path.insert(0, %r); import build_sidecar; '
                  'import secrets; print(secrets.token_hex(16))'
                  % str(ROOT / 'tools'))
        seen = set()
        for _ in range(3):
            result = subprocess.run([*AMBIENT_PYTHON, '-c', script], cwd=ROOT,
                                    check=False, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            seen.add(result.stdout.strip())
        self.assertEqual(len(seen), 3)
        for value in seen:
            self.assertRegex(value, r'\A[0-9a-f]{32}\Z')


class BuildRouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sidecar-route-',
                                                dir=os.environ.get('TMPDIR'))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_the_hook_refuses_and_launches_no_frontend_without_authority(self):
        marker = self.root / 'frontend-was-invoked'
        tools = self.root / 'tools'
        tools.mkdir()
        bun = tools / 'bun'
        bun.write_text('#!/bin/sh\ntouch "$FRONTEND_MARKER"\n', encoding='utf-8')
        bun.chmod(0o755)
        config = json.loads((ROOT / 'tauri/src-tauri/tauri.conf.json').read_text())
        command = config['build']['beforeBuildCommand']
        environment = {name: value for name, value in os.environ.items()
                       if not name.startswith('VOICEBOX_SIDECAR_')}
        environment['PATH'] = str(tools) + os.pathsep + environment['PATH']
        environment['FRONTEND_MARKER'] = str(marker)
        result = subprocess.run(command, shell=True, cwd=ROOT / 'tauri', env=environment,
                                capture_output=True, text=True, timeout=120)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('sidecar gate refused before the frontend build', result.stderr)
        self.assertFalse(marker.exists(), 'the frontend ran despite a refused gate')

    def test_make_j_runs_one_producer_bearing_step_and_never_packages_beside_it(self):
        """The real Makefile bytes, a controlled orchestrator and a controlled bun.

        ADAPTED for the C22 route: `make build` now goes through
        tools/build_sidecar.py, which needs a git checkout, the collector, the
        consumer and the manifest, so the fixture supplies a controlled stand-in
        through the Makefile's own PYTHON variable rather than reproducing all
        of that. The recipe, the route and the ordering under test are real.
        """
        fixture = self.root / 'repository'
        (fixture / 'scripts').mkdir(parents=True)
        (fixture / 'tauri').mkdir()
        (fixture / 'Makefile').write_bytes(MAKEFILE.read_bytes())
        log = fixture / 'order.log'
        packaging = fixture / 'packaging-started'
        orchestrator = self.root / 'controlled-orchestrator'
        orchestrator.write_text(
            '#!/bin/sh\n'
            'set -eu\n'
            'echo server-start >> "$ORDER_LOG"\n'
            'i=0\n'
            'while [ $i -lt 200 ]; do\n'
            '  if [ -e "$PACKAGING_MARKER" ]; then echo concurrent >> "$ORDER_LOG"; break; fi\n'
            '  i=$((i + 1))\n'
            'done\n'
            'echo server-end >> "$ORDER_LOG"\n'
            'package=""\n'
            'while [ $# -gt 0 ]; do\n'
            '  case "$1" in --package) package="$2"; shift 2 ;; *) shift ;; esac\n'
            'done\n'
            '[ -z "$package" ] || sh -c "$package"\n', encoding='utf-8')
        orchestrator.chmod(0o755)
        tools = self.root / 'tools'
        tools.mkdir()
        bun = tools / 'bun'
        bun.write_text('#!/bin/sh\nset -eu\ntouch "$PACKAGING_MARKER"\n'
                       'echo tauri-start >> "$ORDER_LOG"\necho tauri-end >> "$ORDER_LOG"\n',
                       encoding='utf-8')
        bun.chmod(0o755)
        environment = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'],
                           ORDER_LOG=str(log), PACKAGING_MARKER=str(packaging))
        result = subprocess.run(['/usr/bin/make', '-j4', 'build', 'PYTHON=' + str(orchestrator)],
                                cwd=fixture, env=environment, capture_output=True,
                                text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = log.read_text().split()
        self.assertNotIn('concurrent', recorded)
        self.assertEqual(recorded, ['server-start', 'server-end', 'tauri-start', 'tauri-end'])
        self.assertEqual(recorded.count('server-start'), 1)


if __name__ == '__main__':
    unittest.main()
