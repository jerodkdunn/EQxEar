# SPDX-License-Identifier: Apache-2.0
"""Exercise both desktop escaping layers through GLib's real launcher."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio

ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_desktop_launch_round_trips_checkout_paths(self):
        names = ['ordinary', 'space and 雪', 'quote" apostrophe\' backslash\\ dollar$ tick` percent% field%f equals=', 'tab\tand newline\n']
        for name in names:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                checkout = base / name
                checkout.mkdir()
                shutil.copy2(ROOT / 'install-launcher', checkout / 'install-launcher')
                # Use the real run script; replace only the imported application.
                shutil.copy2(ROOT / 'run', checkout / 'run')
                package = checkout / 'eqxear'
                package.mkdir()
                (package / '__main__.py').write_text(
                    'import json, os\nfrom pathlib import Path\n'
                    'Path(os.environ["EQXEAR_LAUNCH_RESULT"]).write_text(json.dumps(os.getcwd()))\n')
                result = base / 'result.json'
                env = dict(os.environ, XDG_DATA_HOME=str(base / 'data'))
                subprocess.run(['python3', str(checkout / 'install-launcher')], env=env, check=True, capture_output=True)
                desktop = base / 'data/applications/com.eqxear.App.desktop'
                if shutil.which('desktop-file-validate'):
                    subprocess.run(['desktop-file-validate', str(desktop)], check=True, capture_output=True)
                app = Gio.DesktopAppInfo.new_from_filename(str(desktop))
                self.assertIsNotNone(app)
                context = Gio.AppLaunchContext()
                context.setenv('EQXEAR_LAUNCH_RESULT', str(result))
                self.assertTrue(app.launch([], context))
                deadline = time.monotonic() + 5
                while not result.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(result.exists(), desktop.read_text())
                self.assertEqual(json.loads(result.read_text()), str(checkout))


if __name__ == '__main__':
    unittest.main()
