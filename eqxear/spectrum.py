# SPDX-License-Identifier: Apache-2.0
"""Output-monitor capture and calibrated stereo, third-octave FFT analysis."""
import array
import atexit
import math
import subprocess
import threading
import weakref
import time

from .fft import RealFFT

CENTERS = tuple(20 * 1000**(i/30) for i in range(31))
EDGES = (20,) + tuple(math.sqrt(a*b) for a,b in zip(CENTERS, CENTERS[1:])) + (20000,)


class Spectrum:
    def __init__(self, size=8192, rate=48000):
        self.size = size
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError('Sample rate must be positive and finite')
        self.fft = RealFFT(size)
        self.input, self.output = self.fft.input, self.fft.output
        self.window = tuple(.5-.5*math.cos(2*math.pi*i/size) for i in range(size))
        self.norm = 2/(size*sum(w*w for w in self.window))
        step = rate/size
        self.bins = []
        for lo, hi in zip(EDGES, EDGES[1:]):
            self.bins.append([(k, max(0, min(hi,(k+.5)*step)-max(lo,(k-.5)*step))/step)
                              for k in range(max(1,int(lo/step-.5)), min(size//2,int(hi/step+.5))+1)
                              if (k+.5)*step > lo and (k-.5)*step < hi])

    def analyze(self, samples):
        if len(samples) != self.size*2:
            raise ValueError('Expected one complete interleaved stereo frame')
        power = [0.] * 31
        for channel in (0, 1):
            for i, w in enumerate(self.window):
                self.input[i] = samples[2*i+channel]*w
            self.fft.execute()
            for j, bins in enumerate(self.bins):
                power[j] += sum((self.output[2*k]**2+self.output[2*k+1]**2)*weight for k,weight in bins)*self.norm/2
        return tuple(max(-90., 10*math.log10(max(1e-12,p))) for p in power)

    def close(self):
        self.fft.close()


# Active workers hold their sessions alive. Weak references avoid retaining
# stopped sessions while still covering children detached during asynchronous Stop.
_SESSIONS = weakref.WeakSet()
_SESSIONS_LOCK = threading.Lock()
_EXITING = threading.Event()


def _shutdown_at_exit():
    # Python joins non-daemon threads before calling atexit. Workers must be
    # daemon threads, with explicit cleanup here for callers that forget Stop.
    with _SESSIONS_LOCK:
        _EXITING.set()
        sessions = list(_SESSIONS)
    for session in sessions:
        session.cancel.set()
    for session in sessions:
        session.ready.wait(timeout=1)
        session.shutdown()


atexit.register(_shutdown_at_exit)


class _Capture:
    def __init__(self):
        self.cancel = threading.Event()
        self.process = None
        self.cleanup_lock = threading.Lock()
        self.ready = threading.Event()
        with _SESSIONS_LOCK:
            if _EXITING.is_set():
                self.cancel.set()
            _SESSIONS.add(self)

    def shutdown(self):
        # The reader and stop's reaper can arrive together. Only one owns wait.
        with self.cleanup_lock:
            process = self.process
            if process is None:
                return
            try:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass


class Analyzer:
    """Owns only a monitor recording stream. Never changes playback routing."""
    def __init__(self):
        self.levels = (-90.,)*31
        self.message = 'Analyzer idle'
        self.source = None
        self.process = None
        self.generation = 0
        self.retry_at = 0.
        self._retry_source = None
        self._lock = threading.Lock()
        self._capture = None

    @staticmethod
    def _cancel(capture):
        if capture is not None:
            capture.cancel.set()
            # A blocked pipe read must not prevent escalation to SIGKILL.
            # The reader owns pipe closure; closing it here could itself block.
            threading.Thread(target=capture.shutdown, daemon=True).start()

    def set_source(self, source):
        with self._lock:
            if source == self.source or (source and source == self._retry_source and time.monotonic() < self.retry_at):
                return
            previous = self._capture
            if previous:
                previous.cancel.set()
            self.generation += 1
            self.source, self.process = source, None
            self.levels = (-90.,)*31
            capture = _Capture() if source else None
            self._capture = capture
            self.message = 'Listening to output…' if source else 'Analyzer idle'
            if capture:
                threading.Thread(target=self._read, args=(source,capture), daemon=True).start()
        self._cancel(previous)

    def stop(self):
        with self._lock:
            capture, self._capture = self._capture, None
            if capture:
                capture.cancel.set()
            self.generation += 1
            self.source = self.process = None
            self.levels = (-90.,)*31
            self.message = 'Analyzer idle'
        self._cancel(capture)

    def _read(self, source, capture):
        fft = process = None
        try:
            fft = Spectrum()
            if capture.cancel.is_set():
                return
            # WirePlumber otherwise makes an explicitly selected monitor follow
            # the default sink when that monitor was initially the default.
            # Recreate capture on device changes; never fall back to a microphone.
            process = subprocess.Popen(['parec','--raw','--format=float32le','--rate=48000','--channels=2',
                                        '--latency-msec=40',f'--device={source}',
                                        '--property=node.dont-reconnect=true',
                                        '--property=node.dont-fallback=true',
                                        '--client-name=EQxEar spectrum','--stream-name=Output spectrum'],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            capture.process = process
            capture.ready.set()
            with self._lock:
                if capture is not self._capture or capture.cancel.is_set():
                    return
                self.process = process
            samples = array.array('f', [0.] * (fft.size*2))
            pending = bytearray()
            while not capture.cancel.is_set():
                chunk = process.stdout.read(2048*8-len(pending))
                if not chunk:
                    raise RuntimeError('Output monitor unavailable. Refresh outputs to reconnect.')
                pending.extend(chunk)
                if len(pending) < 2048*8:
                    continue
                block = array.array('f'); block.frombytes(pending); pending.clear()
                samples = samples[len(block):] + block
                levels = fft.analyze(samples)
                with self._lock:
                    if capture is not self._capture or capture.cancel.is_set():
                        break
                    self.levels = tuple(old+(new-old)*(.8 if new>old else .16) for old,new in zip(self.levels,levels))
                    self.message = 'Output spectrum · dBFS'
        except Exception as error:
            with self._lock:
                if capture is self._capture and not capture.cancel.is_set():
                    self.message = f'RTA unavailable: {error}'
                    self.levels = (-90.,)*31
                    self.source = self.process = None
                    self._capture = None
                    self._retry_source = source
                    self.retry_at = time.monotonic()+5
        finally:
            capture.ready.set()
            if process:
                capture.shutdown()
                try:
                    process.stdout.close()
                except OSError:
                    pass
            if fft:
                fft.close()


def draw_bars(cr, levels, color, left, top, right, bottom, alpha=1):
    cr.set_source_rgba(*(int(color[i:i+2],16)/255 for i in (1,3,5)), alpha)
    for lo, hi, level in zip(EDGES, EDGES[1:], levels):
        x1 = left+math.log(lo/20)/math.log(1000)*(right-left)
        x2 = left+math.log(hi/20)/math.log(1000)*(right-left)
        height = max(0,min(1,(level+90)/90))*(bottom-top)
        cr.rectangle(x1+1,bottom-height,max(1,x2-x1-2),height)
    cr.fill()
