# SPDX-License-Identifier: Apache-2.0
"""Staged package runtime needs neither C sources, compiler, nor writable cache."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from eqxear import build, fft
from tests.test_native import Plugin


class PackagedNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Compile once as package construction would. Runtime tests below forbid
        # subprocesses and give the application no compiler in PATH.
        cls.workspace = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.workspace.cleanup)
        cls.root = Path(cls.workspace.name)
        with patch.dict(os.environ, {'XDG_CACHE_HOME':str(cls.root/'build-cache')}):
            cls.lv2 = build.build_plugin()/'eqxear.lv2'
            cls.fft_binary = fft.build_fft()

    def stage(self, root, packaged=True):
        shutil.copytree(self.lv2, root/'native/lv2/eqxear.lv2')
        shutil.copyfile(self.fft_binary, root/'native/libeqxear_fft.so')
        if packaged:
            (root/'PACKAGED').write_text('Installed native artifacts\n')

    def test_installed_runtime_uses_prebuilt_artifacts_without_sources_or_compiler(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.stage(root)
            cache=root/'must-not-create-cache'
            with patch.object(build,'ROOT',root), patch.object(fft,'ROOT',root), \
                 patch.dict(os.environ,{'PATH':'','XDG_CACHE_HOME':str(cache)}), \
                 patch('subprocess.run',side_effect=AssertionError('runtime compiler invoked')):
                self.assertEqual(build.build_plugin(),root/'native/lv2')
                self.assertEqual(fft.build_fft(),root/'native/libeqxear_fft.so')
                transform=fft.RealFFT(8)
                try:
                    transform.input[0]=1
                    transform.execute()
                    for i in range(5):
                        self.assertAlmostEqual(transform.output[2*i],1)
                        self.assertAlmostEqual(transform.output[2*i+1],0)
                finally: transform.close()
                plugin=Plugin()
                try: self.assertAlmostEqual(plugin.measure()[0],0,places=4)
                finally: plugin.close()
                self.assertFalse(cache.exists())
            self.assertFalse((root/'native/eq.c').exists())
            self.assertFalse((root/'native/fft.c').exists())

    def test_prebuilt_artifacts_also_take_priority_without_packaged_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.stage(root,packaged=False)
            with patch.object(build,'ROOT',root), patch.object(fft,'ROOT',root), \
                 patch.object(Path,'read_bytes',side_effect=AssertionError('source read attempted')), \
                 patch('subprocess.run',side_effect=AssertionError('runtime compiler invoked')):
                self.assertEqual(build.build_plugin(),root/'native/lv2')
                self.assertEqual(fft.build_fft(),root/'native/libeqxear_fft.so')

    def test_incomplete_package_reports_missing_artifacts_without_compiling(self):
        for relative in ('native/lv2/eqxear.lv2/eq.so','native/lv2/eqxear.lv2/eq.ttl',
                         'native/lv2/eqxear.lv2/manifest.ttl','native/libeqxear_fft.so'):
            with self.subTest(missing=relative), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                self.stage(root)
                (root/relative).unlink()
                cache=root/'must-not-create-cache'
                with patch.object(build,'ROOT',root), patch.object(fft,'ROOT',root), \
                     patch.dict(os.environ,{'PATH':'','XDG_CACHE_HOME':str(cache)}), \
                     patch.object(Path,'read_bytes',side_effect=AssertionError('source read attempted')), \
                     patch('subprocess.run',side_effect=AssertionError('runtime compiler invoked')):
                    loader=fft.build_fft if relative.endswith('libeqxear_fft.so') else build.build_plugin
                    with self.assertRaisesRegex(RuntimeError,'Reinstall the EQxEar package') as caught:
                        loader()
                    self.assertIn(relative,str(caught.exception))
                    self.assertFalse(cache.exists())
