# SPDX-License-Identifier: Apache-2.0
"""Exec an audio child that terminates if its owning service dies (Linux)."""
import ctypes
import os
import signal
import sys


def main():
    expected_parent = int(sys.argv[1])
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    # Set this in a freshly launched interpreter, never in Popen.preexec_fn.
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    # Cover parent death between Popen and installing the death signal.
    if os.getppid() != expected_parent:
        return
    os.execvp(sys.argv[2], sys.argv[2:])


if __name__ == '__main__':
    main()
