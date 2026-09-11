#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Create a deterministic, allowlisted source release without private materials."""
import argparse
import ast
import gzip
import hashlib
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def version():
    tree = ast.parse((ROOT / 'eqxear/__init__.py').read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__version__' for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('eqxear/__init__.py must define __version__.')


def release_files():
    names = {'Makefile', 'run', 'install-launcher', 'README.md', 'CONTRIBUTING.md',
             'LICENSE', 'NOTICE', 'THIRD_PARTY.md', 'docs/arch-packaging.md',
             'docs/packaging-plan.md', 'docs/releases/0.1.0.md',
             'tools/build.py', 'tools/dist.py'}
    for directory, patterns in (
        ('eqxear', ('*.py',)), ('native', ('*.c',)), ('data', ('*.svg', '*.desktop')),
        ('LICENSES', ('*.txt',)), ('tests', ('*.py',)),
        ('tests/fixtures', ('*.json', '*.txt', '*.md')),
    ):
        for pattern in patterns:
            names.update(str(p.relative_to(ROOT)) for p in (ROOT / directory).rglob(pattern))
    paths = [ROOT / name for name in sorted(names)]
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Release source must be a regular file: {path.relative_to(ROOT)}')
    return paths


def build_archive(destination, release_version):
    if not isinstance(release_version, str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:[a-zA-Z0-9.-]+)?', release_version):
        raise ValueError('Expected a release version such as 0.1.0.')
    destination.mkdir(parents=True, exist_ok=True)
    stem = f'eqxear-{release_version}'
    archive = destination / f'{stem}.tar.gz'
    sources = release_files()
    with archive.open('wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w', format=tarfile.PAX_FORMAT) as output:
            for source in sources:
                relative = source.relative_to(ROOT)
                entry = tarfile.TarInfo(f'{stem}/{relative.as_posix()}')
                entry.size = source.stat().st_size
                entry.mode = 0o755 if str(relative) in ('run', 'install-launcher') else 0o644
                entry.mtime = 0
                entry.uid = entry.gid = 0
                with source.open('rb') as content:
                    output.addfile(entry, content)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    archive = build_archive(args.output_dir, version())
    print(archive)
    print('sha256: ' + hashlib.sha256(archive.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
