# SPDX-License-Identifier: Apache-2.0
import array
import atexit
import math
import subprocess
import threading
import weakref


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


class _Playback:
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


class Tone:
    def __init__(self):
        self.frequency = 1000.0
        self.level = -42.0
        self.process = None
        self.thread = None
        self.stop_event = threading.Event()
        self.error = None
        self._lock = threading.Lock()
        self._playback = None

    def start(self):
        playback = _Playback()
        with self._lock:
            previous = self._playback
            if previous:
                previous.cancel.set()
            self.process = self.thread = None
            self._playback = playback
            self.stop_event = playback.cancel
            self.error = None
        if previous:
            threading.Thread(target=previous.shutdown, daemon=True).start()
        try:
            if playback.cancel.is_set():
                playback.ready.set()
                return
            process = subprocess.Popen(['pw-cat', '--playback', '--raw', '--format', 'f32', '--rate', '48000', '--channels', '2', '--latency', '30ms', '--properties', '{ node.name = "eqxear-tone" media.name = "EQxEar tuning tone" }', '-'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, bufsize=0)
        except Exception:
            playback.ready.set()
            with self._lock:
                if self._playback is playback:
                    self._playback = None
            raise
        playback.process = process
        playback.ready.set()
        with self._lock:
            current = self._playback is playback and not playback.cancel.is_set()
            if current:
                self.process = process
                self.thread = threading.Thread(target=self._produce, args=(playback,), daemon=True)
                self.thread.start()
        if not current:
            playback.shutdown()
            process.stdin.close()

    def _produce(self, playback):
        # Never read self.process: a later Start can publish a different pipe.
        process, cancel = playback.process, playback.cancel
        phase, frequency, amplitude = 0.0, self.frequency, 0.0
        try:
            while not cancel.is_set():
                target = 10**(self.level/20)
                block = array.array('f')
                for _ in range(480):
                    frequency += (self.frequency-frequency)*.002
                    amplitude += (target-amplitude)*.002
                    phase = (phase + 2*math.pi*frequency/48000) % (2*math.pi)
                    value = amplitude*math.sin(phase)
                    block.extend((value, value))
                if cancel.is_set():
                    break
                process.stdin.write(block.tobytes())
                process.stdin.flush()
        except (OSError, ValueError, OverflowError) as error:
            with self._lock:
                if self._playback is playback and not cancel.is_set():
                    self.error = f'Tone playback stopped: {error}'
        finally:
            playback.shutdown()
            try:
                process.stdin.close()
            except OSError:
                pass

    def stop(self):
        with self._lock:
            playback, self._playback = self._playback, None
            self.stop_event.set()
            if playback:
                playback.cancel.set()
            self.process = self.thread = None
        if playback:
            # Keep the GTK thread responsive even if a pipe write is blocked.
            threading.Thread(target=playback.shutdown, daemon=True).start()
