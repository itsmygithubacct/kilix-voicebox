"""Whole dependency population checks, beyond package metadata and notices."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('web_dependencies', SOURCE / 'tools/web_dependencies.py')
dependencies = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dependencies)


class DependencyPopulationTests(unittest.TestCase):
    def test_equal_code_population_passes_and_changed_missing_or_extra_code_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'node_modules/react-dom/cjs'
            package.mkdir(parents=True)
            implementation = package / 'react-dom-client.production.js'
            implementation.write_bytes(b'original implementation')
            expected = dependencies.population(root)
            dependencies.require_population(dependencies.population(root), expected)
            for mutation in ('changed', 'missing', 'extra'):
                with self.subTest(mutation=mutation):
                    implementation.write_bytes(b'original implementation')
                    extra = package / 'unlisted.js'
                    extra.unlink(missing_ok=True)
                    if mutation == 'changed':
                        implementation.write_bytes(b'original implementation;globalThis.MARKER=true;')
                    elif mutation == 'missing':
                        implementation.unlink()
                    else:
                        extra.write_bytes(b'undeclared code')
                    with self.assertRaisesRegex(ValueError, 'dependency code population differs'):
                        dependencies.require_population(dependencies.population(root), expected)

    def test_external_link_or_linked_dependency_root_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            modules = root / 'node_modules'
            modules.mkdir()
            (modules / 'external').symlink_to('/usr/bin/python3')
            with self.assertRaisesRegex(ValueError, 'external dependency link'):
                dependencies.population(root)
            (modules / 'external').unlink()
            modules.rmdir()
            modules.symlink_to('/tmp')
            with self.assertRaisesRegex(ValueError, 'linked dependency root'):
                dependencies.population(root)
