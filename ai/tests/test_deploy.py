"""Pure rendering checks for device service templates."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "posix", "systemd installation targets Linux")
class DeploymentTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[1] / "deploy/install_services.py"
        spec = importlib.util.spec_from_file_location("install_services_under_test", source)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_service_uses_supplied_user_paths_and_restart_policy(self):
        unit = self.module.render_service("Example", "service-user", "/srv/app", "/srv/app/python server.py", ("STT_MODEL_SIZE=small",))
        self.assertIn("User=service-user\n", unit)
        self.assertIn("WorkingDirectory=/srv/app\n", unit)
        self.assertIn("ExecStart=/srv/app/python server.py\n", unit)
        self.assertIn("Environment=STT_MODEL_SIZE=small\n", unit)
        self.assertIn("Restart=always\n", unit)
        self.assertNotIn("tailscaled", unit)

    def test_unsafe_systemd_paths_are_rejected(self):
        for value in ["/srv/app folder", "/srv/app%h", "/srv/app$HOME", '/srv/app"name', "/srv/app\nname"]:
            with self.subTest(path=value), self.assertRaises(ValueError):
                self.module.systemd_path(Path(value))

    def test_interpreter_symlink_keeps_virtual_environment_path(self):
        with tempfile.TemporaryDirectory() as directory:
            interpreter = Path(directory) / "python"
            interpreter.symlink_to(sys.executable)
            self.assertEqual(self.module.systemd_path(interpreter), str(interpreter.absolute()))


if __name__ == "__main__":
    unittest.main()
