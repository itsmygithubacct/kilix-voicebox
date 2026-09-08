"""Controls for the packaging capability that replaced the stale receipt id.

These are controlled source-level interface tests. **No PyInstaller, Tauri,
Rust, frontend, package or target run happens here**, no producer is executed,
and nothing is installed. The sidecar "binary" is a few bytes of fixture data;
what is under test is the authorization decision, not a build.

The file is organised so intent is never ambiguous:

* ``ParentFalsePass`` reproduces worker 7's reproductions against the **parent**
  bytes extracted from ``4c286b96``, so the defect is demonstrated, not assumed;
* ``CorrectedRefusal`` proves the corrected gate refuses each of those shapes;
* ``CorrectedSuccess`` proves a legitimate live invocation still passes and the
  capability's lifetime is bounded.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'tools'
PLATFORM = 'x86_64-unknown-linux-gnu'
sys.path.insert(0, str(TOOLS))

import sidecar_authority as authority  # noqa: E402

GIT_ENV = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
               GIT_TERMINAL_PROMPT='0', GIT_AUTHOR_NAME='itsmygithubacct',
               GIT_AUTHOR_EMAIL='itsmygithubacct@users.noreply.github.com',
               GIT_COMMITTER_NAME='itsmygithubacct',
               GIT_COMMITTER_EMAIL='itsmygithubacct@users.noreply.github.com')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Tree:
    """A private fixture checkout with installed artifacts and a receipt."""

    def __init__(self, base, *, gate_source):
        self.root = Path(base) / 'checkout'
        (self.root / 'tools').mkdir(parents=True)
        self.binaries = self.root / 'tauri/src-tauri/binaries'
        self.binaries.mkdir(parents=True)
        self.resources = self.root / 'tauri/src-tauri/resources/sidecar'
        self.resources.mkdir(parents=True, mode=0o700)

        for name in ('sidecar_authority.py', 'prepare_sidecar_resources.py',
                     'verify_sidecar_provenance.py'):
            if (TOOLS / name).is_file():
                shutil.copy2(TOOLS / name, self.root / 'tools' / name)
        (self.root / 'tools/tauri_build_gate.py').write_bytes(gate_source)

        self.binary = self.binaries / ('voicebox-server-' + PLATFORM)
        self.provenance = Path(str(self.binary) + '.provenance.json')
        self.receipt = self.resources / 'RESOURCES.json'
        self.build_id = 'a1b2c3d4e5f60718'
        self.write_artifacts(b'ORIGINAL SIDECAR BYTES')

        subprocess.run(['git', 'init', '-q', str(self.root)], env=GIT_ENV, check=True)
        subprocess.run(['git', '-C', str(self.root), 'add', '-A'], env=GIT_ENV, check=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'fixture'],
                       env=GIT_ENV, check=True)
        self.commit = subprocess.run(['git', '-C', str(self.root), 'rev-parse', 'HEAD'],
                                     env=GIT_ENV, capture_output=True, text=True,
                                     check=True).stdout.strip()
        self.tree = subprocess.run(['git', '-C', str(self.root), 'rev-parse', 'HEAD^{tree}'],
                                   env=GIT_ENV, capture_output=True, text=True,
                                   check=True).stdout.strip()

    def write_artifacts(self, binary_bytes, *, notice=b'NOTICE: fixture\n',
                        provenance_extra=None):
        self.binary.write_bytes(binary_bytes)
        provenance = {'schema': 'kilix.voicebox.sidecar-provenance/candidate-v2',
                      'build_id': self.build_id,
                      'binary_sha256': sha256(binary_bytes),
                      'binary_bytes': len(binary_bytes)}
        if provenance_extra:
            provenance.update(provenance_extra)
        self.provenance.write_text(json.dumps(provenance, sort_keys=True) + '\n')
        (self.resources / 'NOTICE.txt').write_bytes(notice)
        receipt = {'schema': 'kilix.voicebox.sidecar-resources/candidate-v1',
                   'build_id': self.build_id, 'source_commit': getattr(self, 'commit', 'x' * 40),
                   'sidecar_sha256': sha256(binary_bytes),
                   'sidecar_bytes': len(binary_bytes),
                   'resources': [{'path': 'NOTICE.txt', 'bytes': len(notice),
                                  'sha256': sha256(notice)}]}
        self.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')

    def refresh_receipt_commit(self):
        receipt = json.loads(self.receipt.read_text())
        receipt['source_commit'] = self.commit
        self.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')

    def resource_rows(self):
        return json.loads(self.receipt.read_text())['resources']

    def run_gate(self, environment):
        env = dict(os.environ, VOICEBOX_SIDECAR_PLATFORM=PLATFORM,
                   PYTHONDONTWRITEBYTECODE='1')
        env.update(environment)
        return subprocess.run([sys.executable, '-I', str(self.root / 'tools/tauri_build_gate.py')],
                              cwd=str(self.root), env=env, capture_output=True, text=True,
                              timeout=120)


def parent_gate_bytes():
    """The exact parent gate, extracted from the candidate commit."""
    return subprocess.run(
        ['git', '-C', str(ROOT), 'show',
         '4c286b96df809c6c263fffdfa0985d60401f7279:tools/tauri_build_gate.py'],
        capture_output=True, check=True).stdout


def current_gate_bytes():
    return (TOOLS / 'tauri_build_gate.py').read_bytes()


class ParentFalsePass(unittest.TestCase):
    """Worker 7's reproductions, against the parent bytes. These SHOULD pass."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='c24-parent-')
        self.addCleanup(self.temp.cleanup)
        self.tree = Tree(self.temp.name, gate_source=parent_gate_bytes())
        self.tree.refresh_receipt_commit()

    def test_replayed_receipt_id_alone_authorizes_packaging(self):
        result = self.tree.run_gate({'VOICEBOX_SIDECAR_GATE_PASSED': self.tree.build_id})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('already passed for build', result.stdout)

    def test_replayed_receipt_authorizes_a_swapped_binary(self):
        """The decisive arm: different bytes, same id, still exit 0."""
        original = self.tree.binary.read_bytes()
        self.tree.binary.write_bytes(b'DIFFERENT BYTES ENTIRELY')
        self.assertNotEqual(sha256(self.tree.binary.read_bytes()), sha256(original))
        recorded = json.loads(self.tree.receipt.read_text())['sidecar_sha256']
        self.assertNotEqual(recorded, sha256(self.tree.binary.read_bytes()))
        result = self.tree.run_gate({'VOICEBOX_SIDECAR_GATE_PASSED': self.tree.build_id})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_shortcut_variable_is_the_whole_authority(self):
        parent = parent_gate_bytes().decode()
        self.assertIn('VOICEBOX_SIDECAR_GATE_PASSED', parent)
        self.assertNotIn('sidecar_authority', parent)


class CorrectedRefusal(unittest.TestCase):
    """The same shapes against the corrected gate. All must refuse."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='c24-fixed-')
        self.addCleanup(self.temp.cleanup)
        self.tree = Tree(self.temp.name, gate_source=current_gate_bytes())
        self.tree.refresh_receipt_commit()
        self.outside = Path(self.temp.name) / 'authority'

    def issue(self, **over):
        arguments = dict(build_id=self.tree.build_id, source_commit=self.tree.commit,
                         source_tree=self.tree.tree, platform=PLATFORM,
                         binary=self.tree.binary, provenance=self.tree.provenance,
                         receipt=self.tree.receipt, resources=self.tree.resource_rows(),
                         root=self.tree.root)
        arguments.update(over)
        return authority.issue(self.outside, **arguments)

    def assert_refused(self, result, fragment):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('sidecar gate refused before the frontend build', result.stderr)
        self.assertIn(fragment, result.stderr)
        self.assertNotIn('already passed', result.stdout)

    def test_stale_inherited_receipt_id_is_no_longer_authority(self):
        """The variable that used to authorize now authorizes nothing."""
        result = self.tree.run_gate({'VOICEBOX_SIDECAR_GATE_PASSED': self.tree.build_id})
        # With no capability it falls through to the standalone path, which
        # refuses for missing explicit inputs -- it never reports success.
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('already passed', result.stdout)

    def test_replay_after_the_issuer_exits_refuses(self):
        script = textwrap.dedent('''
            import sys
            sys.path.insert(0, %r)
            import sidecar_authority as authority
            print(authority.issue(%r, build_id=%r, source_commit=%r, source_tree=%r,
                                  platform=%r, binary=%r, provenance=%r, receipt=%r,
                                  resources=%r, root=%r))
        ''') % (str(self.tree.root / 'tools'), str(self.outside), self.tree.build_id,
                self.tree.commit, self.tree.tree, PLATFORM, str(self.tree.binary),
                str(self.tree.provenance), str(self.tree.receipt),
                self.tree.resource_rows(), str(self.tree.root))
        issued = subprocess.run([sys.executable, '-c', script], capture_output=True,
                                text=True, check=True)
        path = issued.stdout.strip()
        self.assertTrue(Path(path).is_file())
        result = self.tree.run_gate({authority.VARIABLE: path})
        self.assert_refused(result, 'no longer running')

    def test_same_byte_stale_resources_still_refuse_once_the_issuer_is_gone(self):
        """Nothing changed on disk; the invocation is simply not live."""
        before = (self.tree.binary.read_bytes(), self.tree.receipt.read_bytes())
        self.test_replay_after_the_issuer_exits_refuses()
        self.assertEqual((self.tree.binary.read_bytes(), self.tree.receipt.read_bytes()),
                         before, 'a refused gate must not touch the artifacts')

    def test_swapped_binary_refuses(self):
        path = self.issue()
        self.tree.binary.write_bytes(b'SWAPPED AFTER ISSUE')
        self.assert_refused(self.tree.run_gate({authority.VARIABLE: str(path)}),
                            'installed sidecar binary')

    def test_swapped_provenance_refuses(self):
        path = self.issue()
        self.tree.provenance.write_text('{"schema": "tampered"}\n')
        self.assert_refused(self.tree.run_gate({authority.VARIABLE: str(path)}),
                            'provenance record')

    def test_swapped_notice_refuses(self):
        path = self.issue()
        (self.tree.resources / 'NOTICE.txt').write_bytes(b'DIFFERENT NOTICE\n')
        self.assert_refused(self.tree.run_gate({authority.VARIABLE: str(path)}),
                            'shipped resource NOTICE.txt')

    def test_swapped_receipt_refuses(self):
        path = self.issue()
        receipt = json.loads(self.tree.receipt.read_text())
        receipt['build_id'] = 'ffffffffffffffff'
        self.tree.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
        self.assert_refused(self.tree.run_gate({authority.VARIABLE: str(path)}),
                            'resource receipt')

    def test_foreign_source_commit_refuses(self):
        path = self.issue(source_commit='0' * 40)
        self.assert_refused(self.tree.run_gate({authority.VARIABLE: str(path)}),
                            'source commit')

    def test_foreign_source_tree_refuses(self):
        path = self.issue(source_tree='0' * 40)
        self.assert_refused(self.tree.run_gate({authority.VARIABLE: str(path)}),
                            'source tree')

    def test_foreign_platform_refuses(self):
        path = self.issue(platform='aarch64-unknown-linux-gnu')
        self.assert_refused(self.tree.run_gate({authority.VARIABLE: str(path)}),
                            'platform')

    def test_malformed_authority_refuses(self):
        path = self.issue()
        Path(path).write_text('{ not json at all')
        self.assert_refused(self.tree.run_gate({authority.VARIABLE: str(path)}),
                            'not readable JSON')

    def test_linked_authority_refuses(self):
        path = self.issue()
        link = Path(self.temp.name) / 'authority-link.json'
        link.symlink_to(path)
        self.assert_refused(self.tree.run_gate({authority.VARIABLE: str(link)}),
                            'symbolic link')

    def test_group_readable_authority_refuses(self):
        path = self.issue()
        os.chmod(path, 0o640)
        self.assert_refused(self.tree.run_gate({authority.VARIABLE: str(path)}),
                            'group- or world-accessible')

    def test_authority_inside_the_checkout_refuses(self):
        inside = self.tree.root / 'tools' / 'authority.json'
        path = self.issue()
        shutil.copy2(path, inside)
        os.chmod(inside, 0o600)
        self.assert_refused(self.tree.run_gate({authority.VARIABLE: str(inside)}),
                            'outside the checkout')

    def test_repeated_consumption_refuses(self):
        path = self.issue()
        first = self.tree.run_gate({authority.VARIABLE: str(path)})
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertFalse(Path(path).exists(), 'a consumed capability must be gone')
        second = self.tree.run_gate({authority.VARIABLE: str(path)})
        # Either refusal is correct and both are single-consumption: the file is
        # already gone, or a racing claim lost the rename.
        self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
        self.assertNotIn('already passed', second.stdout)
        self.assertNotIn('honoured a live capability', second.stdout)
        self.assertTrue(
            'the capability is missing' in second.stderr
            or 'could not be claimed' in second.stderr, second.stderr)

    def test_only_one_of_two_racing_consumers_wins_the_capability(self):
        """The direct single-consumption guarantee, without a subprocess."""
        path = self.issue()
        outcomes = []
        for _ in range(2):
            try:
                authority.consume(path, root=self.tree.root,
                                  source_commit=self.tree.commit,
                                  source_tree=self.tree.tree, platform=PLATFORM)
                outcomes.append('won')
            except authority.AuthorityError:
                outcomes.append('refused')
        self.assertEqual(outcomes, ['won', 'refused'])
        self.assertFalse(Path(path).exists())

    def test_expired_authority_refuses(self):
        path = self.issue()
        record = json.loads(Path(path).read_text())
        record['issued_at'] = time.time() - 10_000
        record['expires_at'] = time.time() - 5_000
        Path(path).write_text(json.dumps(record, sort_keys=True) + '\n')
        self.assert_refused(self.tree.run_gate({authority.VARIABLE: str(path)}),
                            'expired')

    def test_recycled_issuer_pid_refuses(self):
        path = self.issue()
        record = json.loads(Path(path).read_text())
        record['issuer_start_ticks'] = int(record['issuer_start_ticks']) + 1
        Path(path).write_text(json.dumps(record, sort_keys=True) + '\n')
        self.assert_refused(self.tree.run_gate({authority.VARIABLE: str(path)}),
                            'recycled')

    def test_a_refused_gate_launches_no_frontend_and_changes_nothing(self):
        path = self.issue()
        self.tree.binary.write_bytes(b'SWAPPED AFTER ISSUE')
        before = sorted((p.name, p.stat().st_size)
                        for p in self.tree.resources.iterdir())
        result = self.tree.run_gate({authority.VARIABLE: str(path)})
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('resources prepared for packaging', result.stdout)
        self.assertEqual(sorted((p.name, p.stat().st_size)
                                for p in self.tree.resources.iterdir()), before)


class CorrectedSuccess(unittest.TestCase):
    """A legitimate live invocation still passes, and lifetime stays bounded."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='c24-ok-')
        self.addCleanup(self.temp.cleanup)
        self.tree = Tree(self.temp.name, gate_source=current_gate_bytes())
        self.tree.refresh_receipt_commit()
        self.outside = Path(self.temp.name) / 'authority'

    def issue(self):
        return authority.issue(
            self.outside, build_id=self.tree.build_id, source_commit=self.tree.commit,
            source_tree=self.tree.tree, platform=PLATFORM, binary=self.tree.binary,
            provenance=self.tree.provenance, receipt=self.tree.receipt,
            resources=self.tree.resource_rows(), root=self.tree.root)

    def test_a_live_capability_authorizes_exactly_one_packaging_child(self):
        path = self.issue()
        result = self.tree.run_gate({authority.VARIABLE: str(path)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('honoured a live capability for build ' + self.tree.build_id,
                      result.stdout)
        # No producer or orchestrator was invoked on this path.
        self.assertNotIn('building sidecar', result.stdout)
        self.assertNotIn('resources prepared for packaging', result.stdout)

    def test_the_capability_is_private_and_outside_the_checkout(self):
        path = Path(self.issue())
        info = os.lstat(path)
        self.assertEqual(info.st_mode & 0o777, 0o600)
        self.assertEqual(info.st_uid, os.getuid())
        self.assertFalse(path.resolve().is_relative_to(self.tree.root.resolve()))
        self.assertEqual(os.lstat(path.parent).st_mode & 0o777, 0o700)

    def test_the_token_is_independent_and_unpredictable(self):
        first = json.loads(Path(self.issue()).read_text())
        os.unlink(self.outside / ('authority-' + self.tree.build_id + '.json'))
        second = json.loads(Path(self.issue()).read_text())
        self.assertNotEqual(first['token'], second['token'])
        self.assertEqual(len(first['token']), authority.TOKEN_HEX)
        # The token is nowhere in the tree the gate authorizes.
        published = self.tree.receipt.read_text() + self.tree.provenance.read_text()
        self.assertNotIn(first['token'], published)

    def test_revoke_is_idempotent_and_bounded(self):
        path = self.issue()
        self.assertTrue(authority.revoke(path))
        self.assertFalse(authority.revoke(path))
        self.assertFalse(Path(path).exists())

    def test_consume_burns_the_capability_even_when_validation_fails(self):
        path = self.issue()
        with self.assertRaises(authority.AuthorityError):
            authority.consume(path, root=self.tree.root, source_commit='0' * 40,
                              source_tree=self.tree.tree, platform=PLATFORM)
        self.assertFalse(Path(path).exists(),
                         'a failed validation must still consume the capability')

    def test_unrelated_files_and_processes_are_untouched(self):
        bystander = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(8)'])
        self.addCleanup(lambda: (bystander.kill(), bystander.wait()))
        marker = Path(self.temp.name) / 'unrelated.txt'
        marker.write_text('untouched\n')
        path = self.issue()
        self.assertEqual(self.tree.run_gate({authority.VARIABLE: str(path)}).returncode, 0)
        self.assertEqual(marker.read_text(), 'untouched\n')
        self.assertIsNone(bystander.poll())


if __name__ == '__main__':
    unittest.main()
