# SPDX-License-Identifier: Apache-2.0
"""Installed or cached native FFT and an owned real-to-complex transform."""
import ctypes as C
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import threading

from .build import has_prebuilt

ROOT = Path(__file__).resolve().parents[1]
_BUILD_LOCK = threading.Lock()


def build_fft():
    installed = Path('native/libeqxear_fft.so')
    if has_prebuilt(ROOT, [installed]):
        return ROOT/installed
    source = ROOT/'native/fft.c'
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    cache = Path(os.environ.get('XDG_CACHE_HOME', Path.home()/'.cache'))/'eqxear-v2/fft'
    cache.mkdir(parents=True, exist_ok=True)
    binary = cache/f'fft-{digest}.so'
    with _BUILD_LOCK:
        if not binary.exists():
            fd, temporary = tempfile.mkstemp(prefix='fft-', suffix='.so.tmp', dir=cache)
            os.close(fd)
            try:
                result = subprocess.run(['cc','-std=c11','-O2','-fPIC','-shared','-Wall','-Wextra',
                                         str(source),'-lm','-o',temporary],
                                        capture_output=True, text=True, timeout=30)
                if result.returncode:
                    raise RuntimeError('Could not build the spectrum analyzer. A C compiler is needed.\n'+result.stderr)
                os.replace(temporary, binary)
            finally:
                Path(temporary).unlink(missing_ok=True)
    return binary


class RealFFT:
    def __init__(self, size):
        if not isinstance(size, int) or size < 2 or size > 1<<20 or size & (size-1):
            raise ValueError('FFT size must be a power of two between 2 and 1048576')
        self.size = size
        self.lib = C.CDLL(str(build_fft()))
        self.lib.eqxear_fft_new.argtypes = [C.c_size_t]
        self.lib.eqxear_fft_new.restype = C.c_void_p
        self.lib.eqxear_fft_execute.argtypes = [C.c_void_p, C.POINTER(C.c_double), C.POINTER(C.c_double)]
        self.lib.eqxear_fft_execute.restype = None
        self.lib.eqxear_fft_free.argtypes = [C.c_void_p]
        self.lib.eqxear_fft_free.restype = None
        self.input = (C.c_double*size)()
        self.output = (C.c_double*(2*(size//2+1)))()
        self.handle = self.lib.eqxear_fft_new(size)
        if not self.handle:
            raise MemoryError('Could not initialize spectrum analyzer')

    def execute(self):
        if not self.handle:
            raise RuntimeError('FFT is closed')
        self.lib.eqxear_fft_execute(self.handle, self.input, self.output)

    def close(self):
        if self.handle:
            self.lib.eqxear_fft_free(self.handle)
            self.handle = None
