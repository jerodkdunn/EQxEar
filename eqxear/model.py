# SPDX-License-Identifier: Apache-2.0
"""Validated presets and standard RBJ parametric EQ math."""
from dataclasses import dataclass, asdict
import cmath
import json
import math
import os
from pathlib import Path
import tempfile

KINDS = ('Bell', 'Lo-shelf', 'Hi-shelf')

def bounded(value, low, high):
    if isinstance(value, bool):
        raise ValueError('Expected a number, not a boolean')
    try:
        n = float(value)
    except (ValueError, TypeError, OverflowError) as e:
        raise ValueError('Expected a finite number') from e
    if not math.isfinite(n) or not low <= n <= high:
        raise ValueError(f'Expected a number between {low} and {high}')
    return n

@dataclass
class Band:
    frequency: float = 1000
    gain: float = 0
    q: float = 1
    kind: str = 'Bell'
    enabled: bool = True

    def validate(self):
        self.frequency = bounded(self.frequency, 20, 20000)
        self.gain = bounded(self.gain, -12, 12)
        self.q = bounded(self.q, .3, 10)
        if self.kind not in KINDS or type(self.enabled) is not bool:
            raise ValueError('Invalid band type or enabled flag')
        return self

    def coefficients(self, rate=48000):
        rate = bounded(rate, 1000, 768000)
        if abs(self.gain) < 1e-12:
            return (1., 0., 0.), (1., 0., 0.)
        a = 10 ** (self.gain / 40)
        frequency = max(20, min(self.frequency, rate*.49))
        w = 2 * math.pi * frequency / rate
        c, alpha = math.cos(w), math.sin(w) / (2 * self.q)
        if self.kind == 'Bell':
            return (1+alpha*a, -2*c, 1-alpha*a), (1+alpha/a, -2*c, 1-alpha/a)
        t = 2 * math.sqrt(a) * alpha
        if self.kind == 'Lo-shelf':
            return (a*((a+1)-(a-1)*c+t), 2*a*((a-1)-(a+1)*c), a*((a+1)-(a-1)*c-t)), ((a+1)+(a-1)*c+t, -2*((a-1)+(a+1)*c), (a+1)+(a-1)*c-t)
        return (a*((a+1)+(a-1)*c+t), -2*a*((a-1)+(a+1)*c), a*((a+1)+(a-1)*c-t)), ((a+1)-(a-1)*c+t, 2*((a-1)-(a+1)*c), (a+1)-(a-1)*c-t)

    def response(self, frequency, rate=48000):
        rate = bounded(rate, 1000, 768000)
        # The preview can extend above a low-rate engine's Nyquist frequency.
        # Hold its endpoint response there instead of displaying aliased lobes.
        frequency = min(bounded(frequency, 0, math.inf), rate/2)
        if not self.enabled or abs(self.gain) < 1e-12:
            return 0
        if frequency == 0:
            return self.gain if self.kind == 'Lo-shelf' else 0
        if frequency == rate/2:
            return self.gain if self.kind == 'Hi-shelf' else 0
        b, a = self.coefficients(rate)
        z = cmath.exp(-2j * math.pi * frequency / rate)
        return 20 * math.log10(abs((b[0]+b[1]*z+b[2]*z*z)/(a[0]+a[1]*z+a[2]*z*z)))

@dataclass
class Profile:
    name: str
    bands: list[Band]
    preamp: float = 0
    muted: bool = False

    def validate(self):
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 80:
            raise ValueError('Use a preset name of 1 to 80 characters')
        if not isinstance(self.bands, list) or any(not isinstance(b, Band) for b in self.bands):
            raise ValueError('Preset bands must be a list of band objects')
        if len(self.bands) > 8:
            raise ValueError('A preset supports up to eight bands')
        self.preamp = bounded(self.preamp, -96, 18)
        if type(self.muted) is not bool:
            raise ValueError('Invalid mute flag')
        for band in self.bands:
            band.validate()
        return self

    def response(self, f, rate=48000):
        rate = bounded(rate, 1000, 768000)
        f = bounded(f, 0, math.inf)
        return sum(b.response(f, rate) for b in self.bands)

    def normalization_preamp(self, rate=48000):
        """Attenuate the combined response, rounding toward more headroom.

        Refine local maxima on a log-frequency grid, including exact band
        centers and the DC/Nyquist endpoints at the actual processing rate.
        """
        rate = bounded(rate, 1000, 768000)
        logs = [math.log(.01) + math.log((rate/2)/.01)*i/4096 for i in range(4097)]
        values = [self.response(math.exp(x), rate) for x in logs]
        peak = max(0, *values, self.response(0, rate), self.response(rate/2, rate),
                   *(self.response(max(20, min(b.frequency, rate*.49)), rate) for b in self.bands if b.enabled))
        for i in range(1, len(logs)-1):
            if values[i] > values[i-1] and values[i] >= values[i+1]:
                lo, hi = logs[i-1], logs[i+1]
                for _ in range(36):
                    a, b = lo+(hi-lo)/3, hi-(hi-lo)/3
                    if self.response(math.exp(a), rate) < self.response(math.exp(b), rate):
                        lo = a
                    else:
                        hi = b
                peak = max(peak, self.response(math.exp((lo+hi)/2), rate))
        attenuation = math.ceil(max(0, peak-1e-8)*10)/10
        if attenuation > 96:
            raise ValueError('This curve needs more than 96 dB of attenuation. Reduce band boosts before normalizing.')
        return -attenuation

    def to_dict(self):
        self.validate()
        return {'version': 1, **asdict(self)}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError('Preset must be an object')
        if type(data.get('version')) is not int or data['version'] != 1:
            raise ValueError('Unsupported preset version')
        if not {'name', 'bands', 'preamp'} <= data.keys():
            raise ValueError('Preset is missing name, bands, or preamp')
        if not isinstance(data['bands'], list) or any(not isinstance(b, dict) for b in data['bands']):
            raise ValueError('Preset bands must be a list of objects')
        try:
            bands = [Band(**b) for b in data['bands']]
        except TypeError as e:
            raise ValueError('Invalid band fields') from e
        return cls(data['name'], bands, data['preamp'], data.get('muted', False)).validate()

    def apo(self):
        kinds = {'Bell': 'PK', 'Lo-shelf': 'LSC', 'Hi-shelf': 'HSC'}
        if self.muted:
            raise ValueError('The fader is muted. Raise it before exporting a numeric EQ preset.')
        return f'Preamp: {self.preamp:.1f} dB\n' + ''.join(f'Filter {i+1}: {"ON" if b.enabled else "OFF"} {kinds[b.kind]} Fc {b.frequency:.1f} Hz Gain {b.gain:.1f} dB Q {b.q:.2f}\n' for i, b in enumerate(self.bands))

def from_marks(start, end, dip=False):
    """Build a correction from two edges on the logarithmic frequency axis."""
    start, end = sorted((bounded(start, 20, 20000), bounded(end, 20, 20000)))
    if start == end:
        raise ValueError('Mark two different frequencies for the correction edges')
    center = math.sqrt(start*end)
    return Band(center, 3 if dip else -3, max(.3, min(10, center/(end-start))))

def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.eqxear-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2, allow_nan=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

class Library:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'eqxear-v2/presets.json'

    @staticmethod
    def decode(text):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError('Preset library must be a list')
        return [Profile.from_dict(p) for p in data]

    def load(self):
        if not self.path.exists():
            legacy = self.path.parent.parent/'eqxear/presets.json'
            if self.path.parent.name == 'eqxear-v2' and legacy.exists():
                profiles = self.decode(legacy.read_text())
                self.save(profiles)
                draft = legacy.with_name('draft.json')
                if draft.exists():
                    try: atomic_json(self.path.with_name('draft.json'), Profile.from_dict(json.loads(draft.read_text())).to_dict())
                    except (ValueError, KeyError, TypeError): pass
                return profiles
            return [Profile('Flat', [])]
        return self.decode(self.path.read_text())

    def save(self, profiles):
        atomic_json(self.path, [p.to_dict() for p in profiles])
