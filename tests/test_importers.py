# SPDX-License-Identifier: Apache-2.0
"""Original examples of the public formats; no upstream parser implementation."""
import copy
import json
from pathlib import Path
import unittest

from eqxear.importers import MAX_IMPORT_BYTES, import_profile
from eqxear.model import Band, Profile

FIXTURES = Path(__file__).with_name('fixtures')


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.session = json.loads((FIXTURES / 'eqbyear-session.json').read_text())
        self.expected = Profile('Imported EQ', [Band(85, 2.5, .7, 'Lo-shelf', False),
                              Band(2350, -4.2, 2.35), Band(9100, 1.5, .8, 'Hi-shelf')], -4.5)

    def test_original_interoperability_fixtures_agree(self):
        for fixture in ('eqbyear-session.json', 'peq.txt'):
            with self.subTest(fixture=fixture):
                self.assertEqual(import_profile((FIXTURES / fixture).read_text()), self.expected)

    def test_browser_controls_are_not_imported(self):
        before = copy.deepcopy(self.session)
        profile = import_profile(json.dumps(self.session), name='Speakers')
        self.assertEqual(profile.name, 'Speakers')
        self.assertEqual(profile.preamp, -4.5)
        self.assertFalse(profile.muted)  # Browser eqOn=false does not mute the system.
        self.assertEqual(profile.bands[0].frequency, 85)
        self.assertEqual(self.session, before)

    def test_own_json_preserves_name_mute_and_disabled_bands(self):
        self.expected.name = 'My EQ'
        self.expected.muted = True
        self.assertEqual(import_profile(json.dumps(self.expected.to_dict())), self.expected)

    def test_own_peq_roundtrip_including_disabled_bands(self):
        self.assertEqual(import_profile(self.expected.apo()), self.expected)
        flat = Profile('Flat', [], -1.2)
        self.assertEqual(import_profile(flat.apo(), name='Flat'), flat)

    def test_peq_accepts_whitespace_comments_case_and_numeric_notation(self):
        text = '\ufeff# Comment\r\n  filter\t42 : off pk fc 1e3 Hz gain +.5 db q 1.25\t # note\r\n'
        self.assertEqual(import_profile(text), Profile('Imported EQ', [Band(1000, .5, 1.25, enabled=False)]))

    def test_accepts_boundaries_and_eight_bands(self):
        self.session['preampDb'] = -96
        band = {'type': 'PK', 'fc': 20, 'gain': -12, 'q': .3, 'enabled': False}
        self.session['bands'] = [copy.deepcopy(band) for _ in range(8)]
        self.assertEqual(len(import_profile(json.dumps(self.session)).bands), 8)
        self.session['preampDb'] = 18
        self.session['bands'][0].update(fc=20000, gain=12, q=10)
        profile = import_profile(json.dumps(self.session))
        self.assertEqual(profile.bands[0], Band(20000, 12, 10, enabled=False))
        self.assertEqual(profile.preamp, 18)

    def test_session_requires_version_one(self):
        for version in (None, False, True, 0, 2, '1', 1.0):
            with self.subTest(version=version):
                self.session['version'] = version
                with self.assertRaisesRegex(ValueError, 'version'):
                    import_profile(json.dumps(self.session))
        del self.session['version']
        with self.assertRaisesRegex(ValueError, 'version'):
            import_profile(json.dumps(self.session))

    def test_rejects_out_of_range_or_invalid_band_fields_without_clamping(self):
        for field, values, label in (
            ('fc', (19, 20001, True, None, '1000'), 'frequency'),
            ('gain', (-12.1, 12.1, False, '3'), 'gain'),
            ('q', (.29, 10.01, [], '1'), 'Q'),
            ('enabled', (0, 1, 'false', None), 'enabled'),
            ('type', ('LS', 'HS', 'LP', None, {}), 'type')):
            for value in values:
                with self.subTest(field=field, value=value):
                    data = copy.deepcopy(self.session)
                    data['bands'][1][field] = value
                    with self.assertRaisesRegex(ValueError, f'Band 2.*{label}'):
                        import_profile(json.dumps(data))

    def test_rejects_unsupported_preamp(self):
        for value in (-96.1, 18.1, True, '2', None):
            with self.subTest(value=value):
                self.session['preampDb'] = value
                with self.assertRaisesRegex(ValueError, 'Preamp'):
                    import_profile(json.dumps(self.session))

    def test_rejects_nonfinite_numeric_values(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            self.session['bands'][0]['gain'] = value
            with self.assertRaisesRegex(ValueError, 'non-finite'):
                import_profile(json.dumps(self.session))
        for text in ('Preamp: 1e999 dB', 'Filter 1: ON PK Fc 1000 Hz Gain 1e999 dB Q 1'):
            with self.assertRaisesRegex(ValueError, 'Line 1'):
                import_profile(text)
        with self.assertRaisesRegex(ValueError, 'Band 1.*gain'):
            import_profile(json.dumps(self.session).replace('-Infinity', '1e999'))

    def test_rejects_more_than_eight_bands_in_both_formats(self):
        self.session['bands'] *= 3
        with self.assertRaisesRegex(ValueError, '8 bands'):
            import_profile(json.dumps(self.session))
        text = '\n'.join(f'Filter {i}: ON PK Fc 1000 Hz Gain 0 dB Q 1' for i in range(1, 10))
        with self.assertRaisesRegex(ValueError, '8 bands'):
            import_profile(text)

    def test_json_shape_and_missing_fields(self):
        for field in ('fc', 'gain', 'q', 'type'):
            data = copy.deepcopy(self.session)
            del data['bands'][0][field]
            with self.assertRaisesRegex(ValueError, f'Band 1.*missing {field}'):
                import_profile(json.dumps(data))
        for text in ('[]', '{', json.dumps({'version': 1, 'preampDb': 0}),
                     json.dumps({'version': 1, 'bands': []}),
                     json.dumps({'version': 1, 'preampDb': 0, 'bands': [None]})):
            with self.subTest(text=text), self.assertRaises(ValueError):
                import_profile(text)

    def test_rejects_ambiguous_json(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            import_profile('{"version":1,"version":1,"preampDb":0,"bands":[]}')
        self.session['preamp'] = 0
        with self.assertRaisesRegex(ValueError, 'mixes'):
            import_profile(json.dumps(self.session))

    def test_peq_never_partially_accepts_unsupported_or_malformed_lines(self):
        bad = ['Include: other.txt', 'Channel: L', 'GraphicEQ: 20 0; 1000 -3',
               'Filter 1: ON LP Fc 1000 Hz Gain 0 dB Q 1',
               'Filter 1: ON PK Fc 1000 Hz Gain 0 dB',
               'Filter 1: OFF PK Fc 1000 Hz Gain 30 dB Q 1',
               'Filter 1: ON PK Fc nan Hz Gain 0 dB Q 1',
               'Filter 1: ON PK Fc 1000 Hz Gain 0 dB Q 1 extra',
               'Filter 0: ON PK Fc 1000 Hz Gain 0 dB Q 1',
               'Preamp: 2 dB', 'Hello there']
        for line in bad:
            with self.subTest(line=line), self.assertRaisesRegex(ValueError, 'Line 2'):
                import_profile('Preamp: 0 dB\n' + line)
        with self.assertRaisesRegex(ValueError, 'unique'):
            import_profile('Filter 1: ON PK Fc 1000 Hz Gain 0 dB Q 1\nFilter 01: OFF PK Fc 100 Hz Gain 1 dB Q 1')

    def test_empty_input_and_input_size_limit(self):
        for text in ('', ' \n# comment', b'Preamp: 0 dB'):
            with self.assertRaises(ValueError):
                import_profile(text)
        for text in (' ' * (MAX_IMPORT_BYTES + 1), '#' + '雪' * (MAX_IMPORT_BYTES // 3 + 1)):
            with self.assertRaisesRegex(ValueError, '1 MiB'):
                import_profile(text)
        with self.assertRaisesRegex(ValueError, 'UTF-8'):
            import_profile('\ud800')
        with self.assertRaises(ValueError):
            import_profile('[' * 2000 + ']' * 2000)


if __name__ == '__main__':
    unittest.main()
