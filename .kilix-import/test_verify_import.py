from __future__ import annotations

import copy
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("verify_import.py")
MODULE_SPEC = importlib.util.spec_from_file_location("verify_import", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
verify = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(verify)


class CuratedImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = verify.repository_root(Path(__file__).resolve().parent)
        cls.provenance = verify.load_provenance(cls.root)

    def refusal(self, provenance, code: str) -> None:
        with self.assertRaises(verify.ImportErrorDetail) as caught:
            verify.validate_provenance(provenance)
        self.assertEqual(caught.exception.code, code)

    @staticmethod
    def initialize_repository(root: Path) -> None:
        verify.git(root, "init", "--quiet")
        verify.git(root, "config", "user.name", "itsmygithubacct")
        verify.git(
            root,
            "config",
            "user.email",
            "itsmygithubacct@users.noreply.github.com",
        )

    @staticmethod
    def commit_all(root: Path, message: str = "fixture") -> None:
        verify.git(root, "add", "--all")
        verify.git(root, "commit", "--quiet", "-m", message)

    def test_provenance_is_exact(self) -> None:
        verify.validate_provenance(self.provenance)

    def test_repository_preserves_import_and_publication_boundaries(self) -> None:
        counts = verify.verify_current_repository(self.root, self.provenance)
        self.assertEqual(counts["source_files"], 406)
        self.assertEqual(counts["source_blob_bytes"], 54600537)
        self.assertEqual(counts["source_risks"], 1)
        self.assertEqual(counts["hygiene_boundary_commits"], 1)
        self.assertEqual(counts["hygiene_boundary_ancestors"], 1)
        self.assertEqual(counts["boundary_source_files"], 406)
        self.assertEqual(counts["source_delta_paths"], 8)
        self.assertEqual(counts["normalized_pngs"], 4)
        self.assertEqual(counts["generated_binaries_removed"], 1)
        self.assertEqual(counts["historical_binary_units"], 1)
        self.assertEqual(counts["historical_matches"], 1)
        self.assertEqual(counts["generator_inputs"], 3)
        self.assertEqual(counts["generator_input_bytes"], 8490356)
        self.assertEqual(counts["matched_generator_inputs"], 0)
        self.assertEqual(counts["generator_input_matches"], 0)
        self.assertEqual(counts["historical_adjudications"], 0)
        self.assertEqual(counts["platform_resource_configs"], 1)
        self.assertEqual(counts["scanner_exception_candidates"], 0)
        self.assertEqual(counts["current_source_risks"], 0)
        self.assertEqual(counts["current_blob_paths"],
                         counts["current_source_files"] + counts["owned_files"])
        self.assertEqual(counts["current_worktree_blobs"],
                         counts["current_blob_paths"])
        self.assertEqual(counts["replace_refs"], 0)
        self.assertEqual(counts["graft_files"], 0)
        self.assertEqual(counts["shallow_repositories"], 0)
        self.assertEqual(counts["hidden_index_paths"], 0)
        self.assertEqual(counts["index_paths"], counts["current_blob_paths"])
        self.assertLessEqual(counts["remotes"], 1)
        self.assertEqual(counts["remote_fetch_urls"], counts["remotes"])
        self.assertEqual(counts["remote_push_urls"], counts["remotes"])

    def test_historical_match_measurement_is_reproducible(self) -> None:
        boundary = self.provenance["hygiene_delta"]["generated_binary_boundary"]
        imported = verify.git_blob(
            self.root,
            self.provenance["import"]["product_snapshot_commit"],
            boundary["path"],
        )
        counts = verify.verify_historical_match_proof(
            self.root,
            self.provenance["import"]["product_snapshot_commit"],
            boundary["path"],
            imported,
            boundary["historical_match_proof"],
        )
        self.assertEqual(counts, {
            "historical_binary_units": 1,
            "historical_matches": 1,
            "generator_inputs": 3,
            "generator_input_bytes": 8490356,
            "matched_generator_inputs": 0,
            "generator_input_matches": 0,
            "historical_adjudications": 0,
        })

    def test_ascii_word_matching_requires_boundaries(self) -> None:
        token = bytes.fromhex("703530")
        content = b"x" + token + b" " + bytes.fromhex("503530") + b"\x00"
        self.assertEqual(verify.ascii_word_match_offsets(content, "703530"), [5])

    def test_audio_artifact_is_risky(self) -> None:
        self.assertEqual(verify.risk_paths(["data/generations/output.wav"]),
                         ["data/generations/output.wav"])

    def test_model_artifact_is_risky(self) -> None:
        self.assertEqual(verify.risk_paths(["models/weights.safetensors"]),
                         ["models/weights.safetensors"])

    def test_database_artifact_is_risky(self) -> None:
        self.assertEqual(verify.risk_paths(["data/voicebox.db"]),
                         ["data/voicebox.db"])

    def test_source_code_is_not_risky(self) -> None:
        self.assertEqual(verify.risk_paths(["backend/main.py"]), [])

    def test_upstream_tree_mutation_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        mutant["upstream"]["tree"] = "0" * 40
        self.refusal(mutant, "UPSTREAM_IDENTITY")

    def test_evidence_document_mutation_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        mutant["accepted_evidence"][0]["document"] = "substitute.md"
        self.refusal(mutant, "EVIDENCE_IDENTITY")

    def test_evidence_digest_mutation_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        mutant["accepted_evidence"][0]["sha256"] = "0" * 64
        self.refusal(mutant, "EVIDENCE_IDENTITY")

    def test_evidence_extra_field_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        mutant["accepted_evidence"][0]["accepted"] = True
        self.refusal(mutant, "EVIDENCE_IDENTITY")

    def test_extra_top_level_claim_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        mutant["release_admitted"] = True
        self.refusal(mutant, "PROVENANCE_KEYS")

    def test_history_policy_mutation_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        mutant["import"]["upstream_history_imported"] = True
        self.refusal(mutant, "IMPORT_BOUNDARY")

    def test_remote_policy_mutation_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        mutant["publication"]["authorized_remote_urls"] = [
            "https://github.com/example/not-voicebox.git"
        ]
        self.refusal(mutant, "PUBLICATION_BOUNDARY")

    def test_hygiene_boundary_mutation_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        mutant["publication"]["hygiene_boundary_commit"] = "0" * 40
        self.refusal(mutant, "PUBLICATION_BOUNDARY")

    def test_remote_policy_accepts_zero_or_one_official_remote(self) -> None:
        scratch = os.environ.get("TMPDIR")
        with tempfile.TemporaryDirectory(prefix="voicebox-remote-", dir=scratch) as temp:
            root = Path(temp)
            verify.git(root, "init", "--quiet")
            policy = self.provenance["publication"]
            self.assertEqual(verify.verify_remote_policy(root, policy), {
                "remotes": 0,
                "remote_fetch_urls": 0,
                "remote_push_urls": 0,
            })
            verify.git(root, "remote", "add", "origin", verify.EXPECTED_PUBLIC_REMOTE)
            self.assertEqual(verify.verify_remote_policy(root, policy), {
                "remotes": 1,
                "remote_fetch_urls": 1,
                "remote_push_urls": 1,
            })

    def test_remote_policy_refuses_unapproved_or_multiple_remotes(self) -> None:
        scratch = os.environ.get("TMPDIR")
        policy = self.provenance["publication"]
        with tempfile.TemporaryDirectory(prefix="voicebox-remote-", dir=scratch) as temp:
            root = Path(temp)
            verify.git(root, "init", "--quiet")
            verify.git(root, "remote", "add", "origin",
                       "https://github.com/example/not-voicebox.git")
            with self.assertRaises(verify.ImportErrorDetail) as caught:
                verify.verify_remote_policy(root, policy)
            self.assertEqual(caught.exception.code, "REMOTE_FETCH_URL")
        with tempfile.TemporaryDirectory(prefix="voicebox-remote-", dir=scratch) as temp:
            root = Path(temp)
            verify.git(root, "init", "--quiet")
            verify.git(root, "remote", "add", "origin", verify.EXPECTED_PUBLIC_REMOTE)
            verify.git(root, "remote", "set-url", "--add", "--push", "origin",
                       "https://github.com/example/not-voicebox.git")
            with self.assertRaises(verify.ImportErrorDetail) as caught:
                verify.verify_remote_policy(root, policy)
            self.assertEqual(caught.exception.code, "REMOTE_PUSH_URL")
        with tempfile.TemporaryDirectory(prefix="voicebox-remote-", dir=scratch) as temp:
            root = Path(temp)
            verify.git(root, "init", "--quiet")
            verify.git(root, "remote", "add", "origin", verify.EXPECTED_PUBLIC_REMOTE)
            verify.git(root, "remote", "add", "mirror", verify.EXPECTED_PUBLIC_REMOTE)
            with self.assertRaises(verify.ImportErrorDetail) as caught:
                verify.verify_remote_policy(root, policy)
            self.assertEqual(caught.exception.code, "REMOTE_POPULATION")

    def test_remote_policy_refuses_transport_and_cr_bypasses(self) -> None:
        scratch = os.environ.get("TMPDIR")
        policy = self.provenance["publication"]
        cases = (
            ("vcs", "REMOTE_VCS_OVERRIDE"),
            ("mirror", "REMOTE_MIRROR"),
            ("push-refspec", "REMOTE_PUSH_REFSPEC"),
            ("follow-tags", "PUSH_FOLLOW_TAGS"),
            ("worktree-follow-tags", "PUSH_FOLLOW_TAGS"),
            ("fetch-cr", "REMOTE_FETCH_URL"),
            ("push-cr", "REMOTE_PUSH_URL"),
        )
        for case, expected_code in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory(
                    prefix="voicebox-remote-", dir=scratch
                ) as temp:
                    root = Path(temp)
                    verify.git(root, "init", "--quiet")
                    verify.git(root, "remote", "add", "origin",
                               verify.EXPECTED_PUBLIC_REMOTE)
                    if case == "vcs":
                        verify.git(root, "config", "remote.origin.vcs",
                                   "definitely-not-a-real-transport")
                    elif case == "mirror":
                        verify.git(root, "config", "remote.origin.mirror", "true")
                    elif case == "push-refspec":
                        verify.git(
                            root,
                            "config",
                            "remote.origin.push",
                            "+refs/heads/*:refs/heads/*",
                        )
                    elif case == "follow-tags":
                        verify.git(root, "config", "push.followTags", "true")
                    elif case == "worktree-follow-tags":
                        verify.git(
                            root, "config", "extensions.worktreeConfig", "true"
                        )
                        verify.git(
                            root,
                            "config",
                            "--worktree",
                            "push.followTags",
                            "true",
                        )
                    elif case == "fetch-cr":
                        verify.git(root, "config", "remote.origin.url",
                                   verify.EXPECTED_PUBLIC_REMOTE + "\r")
                    else:
                        verify.git(root, "config", "remote.origin.pushurl",
                                   verify.EXPECTED_PUBLIC_REMOTE + "\r")
                    with self.assertRaises(verify.ImportErrorDetail) as caught:
                        verify.verify_remote_policy(root, policy)
                    self.assertEqual(caught.exception.code, expected_code)

    def test_provenance_is_loaded_from_head_blob(self) -> None:
        scratch = os.environ.get("TMPDIR")
        with tempfile.TemporaryDirectory(
            prefix="voicebox-head-provenance-", dir=scratch
        ) as temp:
            root = Path(temp)
            self.initialize_repository(root)
            provenance = root / ".kilix-import" / "PROVENANCE.json"
            provenance.parent.mkdir()
            provenance.write_text('{"value":"head"}\n', encoding="utf-8")
            self.commit_all(root)
            provenance.write_text('{"value":"worktree"}\n', encoding="utf-8")
            verify.git(
                root,
                "update-index",
                "--skip-worktree",
                ".kilix-import/PROVENANCE.json",
            )
            self.assertEqual(verify.git(root, "status", "--porcelain").stdout, b"")
            self.assertEqual(verify.load_provenance(root), {"value": "head"})

    def test_licence_identity_is_loaded_from_head_blob(self) -> None:
        scratch = os.environ.get("TMPDIR")
        with tempfile.TemporaryDirectory(
            prefix="voicebox-head-licence-", dir=scratch
        ) as temp:
            root = Path(temp)
            self.initialize_repository(root)
            licence = root / "LICENSE"
            licence.write_bytes(b"unexpected published licence\n")
            self.commit_all(root)
            displayed = b"expected worktree licence\n"
            licence.write_bytes(displayed)
            verify.git(root, "update-index", "--skip-worktree", "LICENSE")
            self.assertEqual(verify.git(root, "status", "--porcelain").stdout, b"")
            with self.assertRaises(verify.ImportErrorDetail) as caught:
                verify.verify_revision_blob_identity(
                    root,
                    "HEAD",
                    "LICENSE",
                    verify.sha256_bytes(displayed),
                    "LICENCE_IDENTITY",
                )
            self.assertEqual(caught.exception.code, "LICENCE_IDENTITY")

    def test_worktree_blob_identity_refuses_clean_filter_smudge(self) -> None:
        scratch = os.environ.get("TMPDIR")
        with tempfile.TemporaryDirectory(
            prefix="voicebox-clean-filter-", dir=scratch
        ) as temp:
            root = Path(temp)
            self.initialize_repository(root)
            attributes = root / ".gitattributes"
            payload = root / "payload.txt"
            attributes.write_text("payload.txt filter=mask\n", encoding="utf-8")
            payload.write_text("malicious published bytes\n", encoding="utf-8")
            self.commit_all(root)
            verify.git(
                root,
                "config",
                "filter.mask.clean",
                "sed s/safe/malicious/",
            )
            verify.git(
                root,
                "config",
                "filter.mask.smudge",
                "sed s/malicious/safe/",
            )
            verify.git(root, "config", "filter.mask.required", "true")
            payload.unlink()
            verify.git(root, "checkout", "--", "payload.txt")
            self.assertEqual(payload.read_text(encoding="utf-8"),
                             "safe published bytes\n")
            self.assertEqual(verify.git(root, "status", "--porcelain").stdout, b"")
            shape = verify.verify_repository_shape(root)
            self.assertEqual(shape["hidden_index_paths"], 0)
            with self.assertRaises(verify.ImportErrorDetail) as caught:
                verify.verify_worktree_blob_identity(
                    root, "HEAD", [".gitattributes", "payload.txt"]
                )
            self.assertEqual(caught.exception.code, "WORKTREE_BLOB_IDENTITY")

    def test_repository_shape_refuses_hidden_index_paths(self) -> None:
        scratch = os.environ.get("TMPDIR")
        cases = ("--skip-worktree", "--assume-unchanged")
        for option in cases:
            with self.subTest(option=option):
                with tempfile.TemporaryDirectory(
                    prefix="voicebox-index-flags-", dir=scratch
                ) as temp:
                    root = Path(temp)
                    self.initialize_repository(root)
                    (root / "fixture.txt").write_text("fixture\n", encoding="utf-8")
                    self.commit_all(root)
                    verify.git(root, "update-index", option, "fixture.txt")
                    with self.assertRaises(verify.ImportErrorDetail) as caught:
                        verify.verify_repository_shape(root)
                    self.assertEqual(caught.exception.code, "INDEX_FLAGS")

    def test_repository_shape_refuses_replace_refs(self) -> None:
        scratch = os.environ.get("TMPDIR")
        with tempfile.TemporaryDirectory(
            prefix="voicebox-replace-ref-", dir=scratch
        ) as temp:
            root = Path(temp)
            self.initialize_repository(root)
            (root / "fixture.txt").write_text("fixture\n", encoding="utf-8")
            self.commit_all(root)
            tree = verify.git(root, "rev-parse", "HEAD^{tree}").stdout.strip()
            parent = verify.git(
                root, "commit-tree", tree.decode("ascii"), "-m", "raw parent"
            ).stdout.decode("ascii").strip()
            verify.git(root, "replace", "--graft", "HEAD", parent)
            raw_ancestry = verify.git(
                root,
                "merge-base",
                "--is-ancestor",
                parent,
                "HEAD",
                check=False,
            )
            self.assertEqual(raw_ancestry.returncode, 1)
            with self.assertRaises(verify.ImportErrorDetail) as caught:
                verify.verify_repository_shape(root)
            self.assertEqual(caught.exception.code, "REPLACE_REFS")

    def test_repository_shape_refuses_graft_file(self) -> None:
        scratch = os.environ.get("TMPDIR")
        with tempfile.TemporaryDirectory(
            prefix="voicebox-graft-file-", dir=scratch
        ) as temp:
            root = Path(temp)
            self.initialize_repository(root)
            (root / "fixture.txt").write_text("fixture\n", encoding="utf-8")
            self.commit_all(root)
            head = verify.git(root, "rev-parse", "HEAD").stdout.decode().strip()
            graft = Path(
                verify.git(root, "rev-parse", "--git-path", "info/grafts")
                .stdout.decode()
                .strip()
            )
            if not graft.is_absolute():
                graft = root / graft
            graft.parent.mkdir(parents=True, exist_ok=True)
            graft.write_text(f"{head}\n", encoding="ascii")
            with self.assertRaises(verify.ImportErrorDetail) as caught:
                verify.verify_repository_shape(root)
            self.assertEqual(caught.exception.code, "GRAFT_FILE")

    def test_repository_shape_refuses_shallow_history(self) -> None:
        scratch = os.environ.get("TMPDIR")
        with tempfile.TemporaryDirectory(
            prefix="voicebox-shallow-", dir=scratch
        ) as temp:
            root = Path(temp)
            self.initialize_repository(root)
            (root / "fixture.txt").write_text("fixture\n", encoding="utf-8")
            self.commit_all(root)
            head = verify.git(root, "rev-parse", "HEAD").stdout.decode().strip()
            shallow = Path(
                verify.git(root, "rev-parse", "--git-path", "shallow")
                .stdout.decode()
                .strip()
            )
            if not shallow.is_absolute():
                shallow = root / shallow
            shallow.write_text(f"{head}\n", encoding="ascii")
            with self.assertRaises(verify.ImportErrorDetail) as caught:
                verify.verify_repository_shape(root)
            self.assertEqual(caught.exception.code, "SHALLOW_REPOSITORY")

    def test_tree_population_refuses_gitlinks(self) -> None:
        scratch = os.environ.get("TMPDIR")
        with tempfile.TemporaryDirectory(
            prefix="voicebox-gitlink-", dir=scratch
        ) as temp:
            root = Path(temp)
            self.initialize_repository(root)
            (root / "fixture.txt").write_text("fixture\n", encoding="utf-8")
            self.commit_all(root)
            commit = verify.git(root, "rev-parse", "HEAD").stdout.strip()
            tree = verify.git(
                root,
                "mktree",
                input_bytes=b"160000 commit " + commit + b"\tvendor\n",
            ).stdout.decode("ascii").strip()
            with self.assertRaises(verify.ImportErrorDetail) as caught:
                verify.tree_population(root, tree)
            self.assertEqual(caught.exception.code, "TREE_ENTRY_TYPE")

    def test_ignore_policy_refuses_external_substitute(self) -> None:
        scratch = os.environ.get("TMPDIR")
        with tempfile.TemporaryDirectory(
            prefix="voicebox-ignore-substitute-", dir=scratch
        ) as temp:
            temporary = Path(temp)
            root = temporary / "repository"
            root.mkdir()
            self.initialize_repository(root)
            (root / ".gitignore").write_text("*.temporary\n", encoding="utf-8")
            self.commit_all(root)
            rule = "tauri/src-tauri/gen/Assets.car"
            external = temporary / "external.ignore"
            external.write_text(f"{rule}\n", encoding="utf-8")
            verify.git(root, "config", "core.excludesFile", str(external))
            target = root / rule
            target.parent.mkdir(parents=True)
            target.touch()
            effective = verify.git(
                root, "check-ignore", "--quiet", "--no-index", "--", rule,
                check=False,
            )
            self.assertEqual(effective.returncode, 0)
            with self.assertRaises(verify.ImportErrorDetail) as caught:
                verify.verify_revision_ignore_policy(root, "HEAD", rule, rule)
            self.assertEqual(caught.exception.code, "CURRENT_IGNORE_RULE")

    def test_product_snapshot_mutation_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        mutant["import"]["product_snapshot_commit"] = "0" * 40
        self.refusal(mutant, "IMPORT_BOUNDARY")

    def test_png_identity_mutation_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        mutant["hygiene_delta"]["normalized_pngs"][0]["current_sha256"] = "0" * 64
        self.refusal(mutant, "HYGIENE_DELTA")

    def test_generated_binary_retention_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        mutant["hygiene_delta"]["generated_binary_boundary"]["head_tracked"] = True
        self.refusal(mutant, "HYGIENE_DELTA")

    def test_previous_exception_acceptance_claim_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        mutant["hygiene_delta"]["generated_binary_boundary"][
            "previous_exception_accepted"
        ] = True
        self.refusal(mutant, "HYGIENE_DELTA")

    def test_historical_offset_mutation_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        mutant["hygiene_delta"]["generated_binary_boundary"][
            "historical_match_proof"
        ]["matches"][0]["offset"] += 1
        self.refusal(mutant, "HYGIENE_DELTA")

    def test_historical_adjudication_claim_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        proof = mutant["hygiene_delta"]["generated_binary_boundary"][
            "historical_match_proof"
        ]
        proof["status"] = "ADJUDICATED"
        proof["independent_adjudications"] = 1
        self.refusal(mutant, "HYGIENE_DELTA")


if __name__ == "__main__":
    unittest.main()
