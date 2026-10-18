"""Uji konfigurasi penyebaran (Vercel: filesystem read-only, hanya /tmp yang bisa ditulis)."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from learnova import paths  # noqa: E402


class DeployPathsTest(unittest.TestCase):
    def test_vercel_uses_tmp(self):
        with mock.patch.dict(os.environ, {"VERCEL": "1"}, clear=False):
            os.environ.pop("LEARNOVA_INSTANCE_PATH", None)
            self.assertEqual(paths.instance_path(), os.path.join(tempfile.gettempdir(), "learnova"))

    def test_explicit_instance_path_wins(self):
        with mock.patch.dict(os.environ, {"VERCEL": "1", "LEARNOVA_INSTANCE_PATH": "/data/x"}):
            self.assertEqual(paths.instance_path(), "/data/x")

    def test_local_uses_project_instance(self):
        env = {k: v for k, v in os.environ.items() if k not in ("VERCEL", "LEARNOVA_INSTANCE_PATH")}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(paths.instance_path(), os.path.join(paths.ROOT, "instance"))

    def test_static_assets_in_public(self):
        for rel in ("css/app.css", "css/landing.css", "js/app.js", "img/logo-icon.png", "img/favicon.png"):
            self.assertTrue(os.path.isfile(os.path.join(paths.ROOT, "public", "static", rel)), rel)


if __name__ == "__main__":
    unittest.main()
