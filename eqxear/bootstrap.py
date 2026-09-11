# SPDX-License-Identifier: Apache-2.0
"""Resolve our private installation explicitly, including with PYTHONSAFEPATH."""
from pathlib import Path
import runpy
import sys


def module_command(module, *arguments):
    if module not in ('service', 'child'):
        raise ValueError('Unsupported EQxEar background entry point')
    return [sys.executable, str(Path(__file__).resolve()), module, *map(str, arguments)]


def main():
    module = sys.argv.pop(1)
    module_command(module)  # Validate the internal entry point before importing.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    runpy.run_module('eqxear.' + module, run_name='__main__', alter_sys=True)


if __name__ == '__main__':
    main()
