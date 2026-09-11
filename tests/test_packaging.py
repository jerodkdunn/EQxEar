# SPDX-License-Identifier: Apache-2.0
"""Check package bytecode paths and version-independent archive contents."""
import importlib.util
import marshal
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def tooling(name):
    spec = importlib.util.spec_from_file_location('eqxear_tool_' + name, ROOT / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackagingTests(unittest.TestCase):
    def test_bytecode_uses_installed_paths_and_checked_hashes(self):
        build = tooling('build')
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / 'source'
            package = source / 'eqxear'
            package.mkdir(parents=True)
            (package / '__init__.py').write_text('def example():\n    return 42\n')
            native = base / 'build/native'
            for name in ('lv2/eqxear.lv2/eq.so', 'lv2/eqxear.lv2/eq.ttl', 'lv2/eqxear.lv2/manifest.ttl', 'libeqxear_fft.so'):
                path = native / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'test build artifact')
            for name in ('run', 'data/eqxear.svg', 'data/com.eqxear.App.desktop', 'LICENSE', 'NOTICE', 'README.md', 'THIRD_PARTY.md', 'CONTRIBUTING.md', 'docs/arch-packaging.md'):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('test fixture\n')
            destination = base / 'stage'
            with patch.object(build, 'ROOT', source):
                build.install(base / 'build', '/usr', str(destination))
            bytecode = next(destination.rglob('*.pyc')).read_bytes()
            self.assertEqual(int.from_bytes(bytecode[4:8], 'little'), 3)
            code = marshal.loads(bytecode[16:])
            def check_paths(value):
                self.assertEqual(value.co_filename, '/usr/lib/eqxear/eqxear/__init__.py')
                for constant in value.co_consts:
                    if isinstance(constant, types.CodeType):
                        check_paths(constant)
            check_paths(code)
            self.assertNotIn(str(base).encode(), bytecode)

    def test_archive_includes_future_release_notes_without_private_docs(self):
        dist = tooling('dist')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for source in dist.release_files():
                path = root / source.relative_to(ROOT)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(source.read_bytes())
            notes = root / 'docs/releases/9.8.7.md'
            notes.write_text('Future release notes\n')
            private = root / 'docs/research/private.md'
            private.parent.mkdir(parents=True)
            private.write_text('Do not distribute\n')
            with patch.object(dist, 'ROOT', root):
                paths = dist.release_files()
            self.assertIn(notes, paths)
            self.assertNotIn(private, paths)
