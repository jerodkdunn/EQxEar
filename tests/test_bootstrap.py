# SPDX-License-Identifier: Apache-2.0
"""Background entry points do not depend on the caller's import path."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from eqxear.bootstrap import module_command


class BootstrapTests(unittest.TestCase):
    def test_safe_path_entry_points_from_unrelated_directory(self):
        with tempfile.TemporaryDirectory(prefix='eqxear path with spaces ') as directory:
            root = Path(directory)
            package = root / 'installed/eqxear'
            package.mkdir(parents=True)
            shutil.copy2(module_command('service')[1], package / 'bootstrap.py')
            (package / '__init__.py').touch()
            for module in ('service', 'child'):
                (package / (module + '.py')).write_text(
                    'import json, os, sys\n'
                    'print(json.dumps([__file__, sys.argv[1:], os.environ.get("PYTHONSAFEPATH")]))\n')
                command = module_command(module, 'space and $literal', 'second')
                command[1] = str(package / 'bootstrap.py')
                result = subprocess.run(command, cwd=root,
                                        env=dict(os.environ, PYTHONSAFEPATH='1', PYTHONPATH=''),
                                        capture_output=True, text=True, check=True, timeout=5)
                source, arguments, safe_path = json.loads(result.stdout)
                self.assertEqual(Path(source), package / (module + '.py'))
                self.assertEqual(arguments, ['space and $literal', 'second'])
                self.assertEqual(safe_path, '1')
