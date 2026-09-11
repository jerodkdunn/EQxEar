#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compile and stage a system installation without touching user data or caches."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eqxear.build import write_metadata


def flags(name, default=''):
    return shlex.split(os.environ.get(name, default))


def compile_native(build_dir):
    plugin = build_dir / 'native/lv2/eqxear.lv2'
    plugin.mkdir(parents=True, exist_ok=True)
    for source, destination, standard in (
        ('eq.c', plugin / 'eq.so', 'gnu11'),
        ('fft.c', build_dir / 'native/libeqxear_fft.so', 'c11'),
    ):
        command = flags('CC', 'cc') + flags('CPPFLAGS') + flags('CFLAGS', '-O2')
        command += [f'-std={standard}', '-fPIC', '-shared', '-Wall', '-Wextra',
                    str(ROOT / 'native' / source)]
        command += flags('LDFLAGS') + ['-lm', '-o', str(destination)]
        subprocess.run(command, check=True)
    write_metadata(plugin)


def copy(source, destination, mode=0o644):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(mode)


def write(destination, content, mode=0o644):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding='utf-8')
    destination.chmod(mode)


def desktop_argument(value):
    value = '"' + ''.join('\\' + c if c in '\\"`$' else c for c in value).replace('%', '%%') + '"'
    return value.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


def install(build_dir, prefix, destdir):
    prefix = Path(prefix)
    if not prefix.is_absolute() or '..' in prefix.parts:
        raise ValueError('PREFIX must be an absolute path without parent traversal.')
    destination = Path(destdir) / prefix.relative_to('/') if destdir else prefix
    package = destination / 'lib/eqxear'
    # Refuse an incomplete build before creating an installation.
    native = build_dir / 'native'
    required = ('lv2/eqxear.lv2/eq.so', 'lv2/eqxear.lv2/manifest.ttl',
                'lv2/eqxear.lv2/eq.ttl', 'libeqxear_fft.so')
    for relative in required:
        if not (native / relative).is_file():
            raise ValueError('Native build is missing. Run make before make install.')
    if not (ROOT / 'data/eqxear.svg').is_file():
        raise ValueError('Application icon data/eqxear.svg is missing.')
    copy(ROOT / 'run', package / 'run', 0o755)
    for source in sorted((ROOT / 'eqxear').rglob('*.py')):
        copy(source, package / source.relative_to(ROOT))
    for relative in required:
        copy(native / relative, package / 'native' / relative, 0o755 if relative.endswith('.so') else 0o644)
    write(package / 'PACKAGED', 'EQxEar system package; native binaries are precompiled.\n')
    # The wrapper has no checkout, staging-directory, or per-user paths.
    write(destination / 'bin/eqxear', '#!/bin/sh\nexec python3 ' + shlex.quote(str(prefix / 'lib/eqxear/run')) + ' "$@"\n', 0o755)
    desktop = (ROOT / 'data/com.eqxear.App.desktop').read_text(encoding='utf-8')
    if prefix != Path('/usr'):
        desktop = '\n'.join('Exec=python3 ' + desktop_argument(str(prefix / 'lib/eqxear/run'))
                            if line.startswith('Exec=') else line for line in desktop.splitlines()) + '\n'
    write(destination / 'share/applications/com.eqxear.App.desktop', desktop)
    copy(ROOT / 'data/eqxear.svg', destination / 'share/icons/hicolor/scalable/apps/com.eqxear.App.svg')
    for name in ('LICENSE', 'NOTICE'):
        copy(ROOT / name, destination / 'share/licenses/eqxear' / name)
    for source in sorted((ROOT / 'LICENSES').glob('*')):
        if source.is_file():
            copy(source, destination / 'share/licenses/eqxear/LICENSES' / source.name)
    for name in ('README.md', 'THIRD_PARTY.md', 'CONTRIBUTING.md'):
        copy(ROOT / name, destination / 'share/doc/eqxear' / name)
    copy(ROOT / 'docs/arch-packaging.md', destination / 'share/doc/eqxear/arch-packaging.md')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'build')
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--prefix', default='/usr')
    parser.add_argument('--destdir', default='')
    args = parser.parse_args()
    if args.install:
        install(args.build_dir, args.prefix, args.destdir)
    else:
        compile_native(args.build_dir)


if __name__ == '__main__':
    main()
