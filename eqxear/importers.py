# SPDX-License-Identifier: Apache-2.0
"""Read supported EQ interchange formats without applying or saving anything.

Original parsers for EQ by Ear version-1 sessions, EQxEar profiles and the
Preamp/Filter subset of Equalizer APO text. Browser playback state is ignored.
"""
import json
import math
import re

from .model import Band, KINDS, Profile

MAX_IMPORT_BYTES = 1024 * 1024
TYPES = {'PK': 'Bell', 'LSC': 'Lo-shelf', 'HSC': 'Hi-shelf'}
NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
PREAMP = re.compile(rf'Preamp\s*:\s*({NUMBER})\s+dB', re.I)
FILTER = re.compile(
    rf'Filter\s+(\d+)\s*:\s*(ON|OFF)\s+(\S+)\s+Fc\s+({NUMBER})\s+Hz\s+'
    rf'Gain\s+({NUMBER})\s+dB\s+Q\s+({NUMBER})', re.I)


def _number(value, low, high, field):
    if type(value) not in (int, float):
        raise ValueError(f'{field}: expected a finite number.')
    try:
        value = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f'{field}: expected a finite number.') from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{field}: EQxEar supports {low:g} to {high:g}; adjust this value before importing.')
    return value


def _band(data, index, session):
    label = f'Band {index}'
    if not isinstance(data, dict):
        raise ValueError(f'{label}: expected a band object.')
    frequency, kind = ('fc', 'type') if session else ('frequency', 'kind')
    for field in (frequency, 'gain', 'q', kind):
        if field not in data:
            raise ValueError(f'{label}: missing {field}.')
    value = data[kind]
    if not isinstance(value, str) or value not in (TYPES if session else KINDS):
        supported = ', '.join(TYPES if session else KINDS)
        raise ValueError(f'{label} type: only {supported} are supported.')
    enabled = data.get('enabled', True)
    if type(enabled) is not bool:
        raise ValueError(f'{label} enabled: expected true or false.')
    return Band(
        _number(data[frequency], 20, 20000, f'{label} frequency (Hz)'),
        _number(data['gain'], -12, 12, f'{label} gain (dB)'),
        _number(data['q'], .3, 10, f'{label} Q'),
        TYPES[value] if session else value, enabled)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'JSON contains a duplicate field: {key}.')
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f'JSON contains a non-finite value: {value}.')


def _json_profile(text, name):
    try:
        data = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
    except json.JSONDecodeError as error:
        raise ValueError(f'Invalid JSON at line {error.lineno}, column {error.colno}.') from None
    except RecursionError:
        raise ValueError('JSON is nested too deeply.') from None
    if not isinstance(data, dict):
        raise ValueError('Expected a single EQ session or profile object, not a preset library.')
    if type(data.get('version')) is not int or data['version'] != 1:
        raise ValueError('Unsupported JSON version; import a version 1 session or profile.')
    if 'preampDb' in data and 'preamp' in data:
        raise ValueError('JSON mixes session preampDb and profile preamp fields.')
    session = 'preampDb' in data
    preamp_field = 'preampDb' if session else 'preamp'
    if preamp_field not in data:
        raise ValueError('JSON is missing preampDb (EQ by Ear) or preamp (EQxEar).')
    bands = data.get('bands')
    if not isinstance(bands, list):
        raise ValueError('JSON bands must be a list of band objects.')
    if len(bands) > 8:
        raise ValueError('EQxEar supports up to 8 bands; remove extra bands before importing.')
    preamp = _number(data[preamp_field], -96, 18, 'Preamp (dB)')
    muted = False if session else data.get('muted', False)
    if type(muted) is not bool:
        raise ValueError('Profile muted: expected true or false.')
    return Profile(name if session else data.get('name', name),
                   [_band(b, i, session) for i, b in enumerate(bands, 1)],
                   preamp, muted).validate()


def _peq_profile(text, name):
    bands, numbers = [], set()
    preamp = 0
    seen_preamp = False
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.partition('#')[0].strip()
        if not line:
            continue
        match = PREAMP.fullmatch(line)
        if match:
            if seen_preamp:
                raise ValueError(f'Line {number}: duplicate Preamp directive.')
            preamp = _number(float(match[1]), -96, 18, f'Line {number} preamp (dB)')
            seen_preamp = True
            continue
        match = FILTER.fullmatch(line)
        if not match:
            raise ValueError(f'Line {number}: unsupported or malformed EQ directive. Use Preamp: -3 dB or Filter 1: ON PK Fc 1000 Hz Gain -3 dB Q 1.')
        # Canonicalize textual identifiers without converting arbitrarily large integers.
        identifier = match[1].lstrip('0')
        if not identifier or identifier in numbers:
            raise ValueError(f'Line {number}: filter numbers must be positive and unique.')
        numbers.add(identifier)
        if len(bands) == 8:
            raise ValueError(f'Line {number}: EQxEar supports up to 8 bands; remove extra filters before importing.')
        try:
            bands.append(_band({'type': match[3].upper(), 'fc': float(match[4]),
                                'gain': float(match[5]), 'q': float(match[6]),
                                'enabled': match[2].upper() == 'ON'}, len(bands) + 1, True))
        except ValueError as error:
            raise ValueError(f'Line {number}: {error}') from None
    if not bands and not seen_preamp:
        raise ValueError('No EQ found. Import an EQ session JSON or Preamp/Filter text.')
    return Profile(name, bands, preamp).validate()


def import_profile(text, name='Imported EQ'):
    """Return a validated profile or raise ValueError with an import explanation.

    No values are clamped, normalized, played, saved, or sent to the engine.
    Session oscillator level/frequency, eqOn, theme and output are not EQ data.
    """
    if not isinstance(text, str):
        raise ValueError('Import requires UTF-8 text.')
    try:
        if len(text) > MAX_IMPORT_BYTES or len(text.encode('utf-8')) > MAX_IMPORT_BYTES:
            raise ValueError('EQ import is limited to 1 MiB.')
    except UnicodeEncodeError:
        raise ValueError('Import requires valid UTF-8 text.') from None
    text = text.lstrip('\ufeff').strip()
    return _json_profile(text, name) if text.startswith(('{', '[')) else _peq_profile(text, name)
