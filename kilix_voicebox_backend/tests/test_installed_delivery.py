"""Installed shell authentication and immutable public-asset boundaries."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from kilix_voicebox_backend import main


class InstalledDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ui = self.root / 'ui'
        self.ui.mkdir(mode=0o700)
        (self.ui / 'index.html').write_bytes(b'<p>public immutable studio shell</p>')
        (self.ui / 'bundle.js').write_bytes(b'console.log("no session credential");')
        self.patch = patch.multiple(main, _installed_ui=None, _installed_origin=None, _installed_assets={})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        main.configure_installed_ui(self.ui, 25417)
        self.client = TestClient(main.app, base_url='http://127.0.0.1:25417')

    def test_public_assets_require_no_credential_and_expose_no_product_data(self):
        response = self.client.get('/ui/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'<p>public immutable studio shell</p>')
        self.assertIn("frame-ancestors 'none'", response.headers['content-security-policy'])
        self.assertEqual(response.headers['referrer-policy'], 'no-referrer')
        self.assertEqual(self.client.get('/profiles').status_code, 401)
        self.assertEqual(self.client.get('/ui/private-store.db').status_code, 404)

    def test_replaced_asset_and_new_files_never_change_the_served_shell(self):
        (self.ui / 'bundle.js').write_bytes(b'changed after startup')
        (self.ui / 'credential.txt').write_bytes(b'private')
        self.assertEqual(self.client.get('/ui/bundle.js').content, b'console.log("no session credential");')
        self.assertEqual(self.client.get('/ui/credential.txt').status_code, 404)

    def test_foreign_origin_is_refused_even_for_public_shell(self):
        self.assertEqual(self.client.get('/ui/', headers={'Origin': 'https://foreign.invalid'}).status_code, 403)

    def test_unsafe_symlink_population_refuses_configuration(self):
        (self.ui / 'linked').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            main.configure_installed_ui(self.ui, 25417)


if __name__ == '__main__':
    unittest.main()
