from __future__ import annotations

import copy
import importlib.util
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

    def test_provenance_is_exact(self) -> None:
        verify.validate_provenance(self.provenance)

    def test_current_repository_reconstructs_upstream(self) -> None:
        counts = verify.verify_current_repository(self.root, self.provenance)
        self.assertEqual(counts["source_files"], 406)
        self.assertEqual(counts["source_blob_bytes"], 54600537)
        self.assertEqual(counts["source_risks"], 1)

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

    def test_history_policy_mutation_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        mutant["import"]["upstream_history_imported"] = True
        self.refusal(mutant, "IMPORT_BOUNDARY")

    def test_remote_policy_mutation_is_refused(self) -> None:
        mutant = copy.deepcopy(self.provenance)
        mutant["import"]["remote_population_expected"] = 1
        self.refusal(mutant, "IMPORT_BOUNDARY")


if __name__ == "__main__":
    unittest.main()
