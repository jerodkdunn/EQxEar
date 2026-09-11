# SPDX-License-Identifier: Apache-2.0
"""Run a smoke test against installed modules, outside the source checkout.

Example: python tests/installed_smoke.py --prefix /usr --test ui_smoke.py
Preloading the installed package fixes its submodule search path even when a
source test adds the checkout to sys.path. Child services also use that path.
"""
import argparse
import importlib
import importlib.util
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, default=Path('/usr'))
    parser.add_argument('--test', choices=('ui_smoke.py', 'integration_v2.py', 'integration_spectrum.py', 'integration_recovery.py', 'integration_rate.py'))
    args = parser.parse_args()
    prefix = args.prefix.resolve()
    application = prefix / 'lib/eqxear'
    test_directory = Path(__file__).resolve().parent
    assert (application / 'PACKAGED').is_file()
    assert not list(application.rglob('*.c')), 'Native source must not be installed'
    for source in (application / 'eqxear').glob('*.py'):
        assert Path(importlib.util.cache_from_source(str(source))).is_file(), source
    sys.path.insert(0, str(application))
    eqxear = importlib.import_module('eqxear')
    assert Path(eqxear.__file__).resolve().is_relative_to(application)
    from eqxear.build import build_plugin
    from eqxear.fft import build_fft, RealFFT
    with tempfile.TemporaryDirectory(prefix='eqxear-installed-') as temporary:
        os.chdir(temporary)
        for variable, name in (('XDG_CACHE_HOME', 'cache'), ('XDG_DATA_HOME', 'data'), ('XDG_CONFIG_HOME', 'config'), ('XDG_RUNTIME_DIR', 'runtime')):
            directory = Path(temporary) / name
            directory.mkdir(mode=0o700)
            os.environ[variable] = str(directory)
        result = subprocess.run([str(prefix / 'bin/eqxear'), '--version'], capture_output=True, text=True, check=True)
        assert result.stdout.strip() == f'EQxEar {eqxear.__version__}', result.stdout
        for name in ('cache', 'data', 'config', 'runtime'):
            assert not list((Path(temporary) / name).iterdir()), f'--version wrote to {name}'
        with patch('subprocess.run', side_effect=AssertionError('Runtime compilation attempted')):
            assert build_plugin().resolve() == application / 'native/lv2'
            assert build_fft().resolve() == application / 'native/libeqxear_fft.so'
            fft = RealFFT(8)
            try:
                fft.input[0] = 1
                fft.execute()
                for index in range(5):
                    assert abs(fft.output[2*index] - 1) < 1e-12
                    assert abs(fft.output[2*index+1]) < 1e-12
            finally:
                fft.close()
        subprocess.run(['desktop-file-validate', str(prefix / 'share/applications/com.eqxear.App.desktop')], check=True)
        assert (prefix / 'share/icons/hicolor/scalable/apps/com.eqxear.App.svg').is_file()
        print('PASS: installed command, native DSP path, FFT execution and desktop assets')
        if args.test:
            sys.path.insert(0, str(test_directory))
            runpy.run_path(str(test_directory / args.test), run_name='__main__')
        for name, module in tuple(sys.modules.items()):
            if name == 'eqxear' or name.startswith('eqxear.'):
                assert Path(module.__file__).resolve().is_relative_to(application), (name, module.__file__)
        assert not list((Path(temporary) / 'cache').rglob('*.so')), 'Installed app compiled a cached library'
        print('PASS: every imported EQxEar module came from the installation')


if __name__ == '__main__':
    main()
