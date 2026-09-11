# SPDX-License-Identifier: Apache-2.0
"""Exercise real compiler processes using a shared empty cache."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from eqxear import build


class BuildTests(unittest.TestCase):
    def test_concurrent_builds_publish_one_complete_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            commands=root/'bin'; commands.mkdir()
            compiler=commands/'cc'
            # Hold every compiler until all builders enter compilation. This
            # reproduces the previous shared-temporary race deterministically.
            compiler.write_text(f'''#!{sys.executable}
import os, pathlib, time
root=pathlib.Path({directory!r})
(root/('entered-'+str(os.getpid()))).touch()
deadline=time.monotonic()+15
while len(list(root.glob('entered-*')))<4:
    if time.monotonic()>deadline: raise SystemExit('compiler barrier timed out')
    time.sleep(.01)
os.execv({shutil.which('cc')!r}, [{shutil.which('cc')!r}]+__import__('sys').argv[1:])
''')
            compiler.chmod(0o700)
            env=dict(os.environ, XDG_CACHE_HOME=str(root/'cache'), PATH=str(commands)+os.pathsep+os.environ['PATH'])
            processes=[subprocess.Popen([sys.executable,'-c','from eqxear.build import build_plugin; print(build_plugin())'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(4)]
            results=[]
            try:
                for process in processes:
                    stdout,stderr=process.communicate(timeout=25)
                    self.assertEqual(process.returncode,0,stderr)
                    results.append(stdout.strip())
            finally:
                for process in processes:
                    if process.poll() is None: process.kill()
                    process.communicate()
            self.assertEqual(len(set(results)),1)
            bundle=Path(results[0])/'eqxear.lv2'
            self.assertEqual({item.name for item in bundle.iterdir()},{'eq.so','eq.ttl','manifest.ttl'})
            self.assertIn('lv2:binary <eq.so>',(bundle/'manifest.ttl').read_text())
            self.assertEqual(list(bundle.parent.parent.glob('.build-*')),[])
            with patch.dict(os.environ,{'XDG_CACHE_HOME':str(root/'cache')}), patch.object(build.subprocess,'run',side_effect=AssertionError('cache miss')):
                self.assertEqual(build.build_plugin(),bundle.parent)

    def test_content_change_rebuilds_even_with_older_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'native').mkdir()
            source=root/'native/eq.c'; source.write_bytes((build.ROOT/'native/eq.c').read_bytes())
            with patch.object(build,'ROOT',root), patch.dict(os.environ,{'XDG_CACHE_HOME':str(root/'cache')}):
                first=build.build_plugin()
                source.write_bytes(source.read_bytes()+b'\n/* different source identity */\n')
                os.utime(source,(1,1))
                second=build.build_plugin()
                self.assertNotEqual(first,second)
                self.assertTrue((first/'eqxear.lv2/eq.so').is_file())
                self.assertTrue((second/'eqxear.lv2/eq.so').is_file())

    def test_failed_metadata_never_publishes_partial_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ,{'XDG_CACHE_HOME':directory}), patch.object(build,'write_metadata',side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError,'disk full'):
                    build.build_plugin()
            self.assertEqual(list((Path(directory)/'eqxear-v2/lv2').iterdir()),[])
