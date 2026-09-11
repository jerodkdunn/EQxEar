# SPDX-License-Identifier: Apache-2.0
import json
import math
from pathlib import Path
import tempfile
import unittest
from eqxear.model import Band, Profile, Library, from_marks
from eqxear.routing import controls

class ModelTests(unittest.TestCase):
    def test_bell_center_and_flat(self):
        self.assertAlmostEqual(Band(1000, 6, 2).response(1000), 6, places=8)
        for f in (20, 500, 20000):
            self.assertAlmostEqual(Band().response(f), 0)

    def test_shelves(self):
        self.assertAlmostEqual(Band(1000, 6, .707, 'Lo-shelf').response(20), 6, places=3)
        self.assertAlmostEqual(Band(1000, -6, .707, 'Hi-shelf').response(20000), -6, places=3)

    def test_overlapping_headroom(self):
        p = Profile('Overlap', [Band(1000, 6), Band(1000, 6)])
        self.assertEqual(p.normalization_preamp(), -12)
        self.assertAlmostEqual(p.response(1000), 12)

    def test_normalize_broad_bass_bell(self):
        p = Profile('Bass', [Band(29, 11.5, .3)])
        p.preamp = p.normalization_preamp()
        self.assertEqual(p.preamp, -11.5)
        self.assertAlmostEqual(p.response(29)+p.preamp, 0, places=7)
        self.assertEqual(p.normalization_preamp(), -11.5)
        self.assertEqual(p.bands[0].gain, 11.5)
        self.assertEqual(controls(p)['preamp'], -11.5)

    def test_normalize_combined_peak(self):
        p = Profile('Overlap', [Band(1000, 6), Band(1000, 6)])
        self.assertEqual(p.normalization_preamp(), -12)
        p = Profile('Separate', [Band(50, 6, 5), Band(5000, 6, 5)])
        self.assertGreater(p.normalization_preamp(), -7)

    def test_normalize_resonant_shelves_and_intermediate_peak(self):
        for bands in ([Band(20, 6, 5, 'Lo-shelf')],
                      [Band(1000, 6, 3), Band(1100, 5, 3)],
                      [Band(18000, 6, 5, 'Hi-shelf')]):
            p = Profile('Resonance', bands)
            preamp = p.normalization_preamp()
            peak = max(p.response(.01*2400000**(i/12000)) for i in range(12001))
            self.assertLessEqual(peak+preamp, 1e-7)
            self.assertGreater(peak+preamp, -.101)

    def test_normalize_limits_and_no_boost(self):
        for bands in ([], [Band(1000, -6)], [Band(1000, 12, enabled=False)]):
            self.assertEqual(Profile('No boost', bands).normalization_preamp(), 0)
        self.assertEqual(Profile('Large boost', [Band(1000, 12)]*4).normalization_preamp(), -48)

    def test_disabled_and_empty(self):
        p = Profile('Empty', [Band(1000, 12, enabled=False)])
        self.assertEqual(p.normalization_preamp(), 0)
        self.assertEqual(p.response(1000), 0)
        self.assertEqual(controls(Profile('Flat', []))['b0_type'], 0)

    def test_marks(self):
        b = from_marks(500, 1500)
        self.assertAlmostEqual(b.frequency, math.sqrt(500*1500), places=10)
        self.assertAlmostEqual(b.q, b.frequency/1000, places=10)
        self.assertEqual(b.gain, -3)
        self.assertEqual(from_marks(1500,500), b)
        self.assertEqual(from_marks(500,1500,dip=True).gain, 3)
        self.assertEqual(from_marks(20,20000).q, .3)
        self.assertEqual(from_marks(1000,1000.1).q, 10)
        for edges in [(1000,1000), (19,1000), (1000,20001),
                      (float('nan'),1000), (20,float('inf')), (None,1000), (True,1000)]:
            with self.subTest(edges=edges), self.assertRaises(ValueError):
                from_marks(*edges)

    def test_shared_response_fixtures(self):
        fixtures = json.loads((Path(__file__).parent/'fixtures/response-cases.json').read_text())
        for case in fixtures['cases']:
            profile = Profile.from_dict(case['profile'])
            for point in case['response_points']:
                with self.subTest(case=case['id'],frequency=point['frequency_hz']):
                    actual = profile.response(point['frequency_hz'],case['sample_rate_hz'])+profile.preamp
                    self.assertAlmostEqual(actual,point['gain_db'],delta=fixtures['tolerance_db'])

    def test_sample_rate_and_frequency_validation(self):
        for rate in (0,999,768001,float('inf'),float('nan'),True,None):
            with self.subTest(rate=rate):
                for fn in (lambda: Band().coefficients(rate),
                           lambda: Band().response(1000,rate),
                           lambda: Profile('Empty',[]).response(1000,rate),
                           lambda: Profile('Empty',[]).normalization_preamp(rate)):
                    with self.assertRaises(ValueError): fn()
        for frequency in (-1,float('inf'),float('nan'),None,True):
            with self.subTest(frequency=frequency), self.assertRaises(ValueError):
                Band().response(frequency)

    def test_rate_specific_endpoints_and_native_frequency_clamp(self):
        for rate in (1000,32000,44100,48000,96000,768000):
            for kind,dc,nyquist in [('Bell',0,0),('Lo-shelf',6,0),('Hi-shelf',0,6)]:
                band = Band(20000,6,5,kind)
                self.assertEqual(band.response(0,rate),dc)
                self.assertEqual(band.response(rate/2,rate),nyquist)
                self.assertEqual(band.response(rate,rate),nyquist)
                clamped = Band(min(20000,rate*.49),6,5,kind)
                self.assertEqual(band.coefficients(rate),clamped.coefficients(rate))
        self.assertEqual(Band(20,0,10).coefficients(768000), ((1.,0.,0.),(1.,0.,0.)))

    def test_combined_normalization_at_actual_sample_rates(self):
        profile = Profile('Treble overlap',[Band(12000,6,.3),Band(20000,6,.3)])
        self.assertEqual(profile.normalization_preamp(48000),-10.1)
        self.assertEqual(profile.normalization_preamp(96000),-11.6)
        for rate in (32000,44100,48000,96000):
            preamp=profile.normalization_preamp(rate)
            peak=max(profile.response(.01*((rate/2)/.01)**(i/6000),rate) for i in range(6001))
            self.assertLessEqual(peak+preamp,1e-7)
            self.assertGreater(peak+preamp,-.101)

    def test_validation(self):
        for b in (Band(float('nan')), Band(gain=13), Band(q=0), Band(kind='garbage')):
            with self.assertRaises(ValueError): Profile('Bad', [b]).validate()
        with self.assertRaises(ValueError): Profile('../name', [], math.inf).validate()
        with self.assertRaises(ValueError): Profile('Bad', [Band()]*9).validate()

    def test_storage_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            lib = Library(Path(d)/'presets.json')
            p = Profile('Speakers / desk', [Band(80, -3, .7, 'Lo-shelf')], -2)
            lib.save([p]); self.assertEqual(lib.load(), [p])
            original = lib.path.read_bytes()
            with self.assertRaises(ValueError): lib.save([Profile('Bad', [], float('nan'))])
            self.assertEqual(lib.path.read_bytes(), original)

    def test_malformed_storage_preserves_originals(self):
        malformed = (None, {}, 1, 'preset', [None], [[]], [{'version': 1}],
                     [{'version': 1, 'name': 'Bad', 'bands': [None], 'preamp': 0}],
                     [{'version': 1, 'name': 'Bad', 'bands': {}, 'preamp': 0}],
                     [{'version': 1, 'name': 'Bad', 'bands': [{'unknown': 1}], 'preamp': 0}],
                     [{'version': 1, 'name': 'Bad', 'bands': [], 'preamp': []}])
        with tempfile.TemporaryDirectory() as d:
            lib = Library(Path(d)/'presets.json')
            for value in malformed:
                with self.subTest(value=value):
                    lib.path.write_text(json.dumps(value))
                    original = lib.path.read_bytes()
                    with self.assertRaises(ValueError): lib.load()
                    self.assertEqual(lib.path.read_bytes(), original)
        for value in (None, [], {}, {'version': True}, {'version': 1, 'name': 'Bad', 'bands': [None], 'preamp': 0}):
            with self.subTest(draft=value), self.assertRaises(ValueError):
                Profile.from_dict(value)

    def test_fader_gain_and_mute_storage(self):
        for gain in (-96, -72, -48, -36, -11.5, 0, 18):
            p = Profile('Fader', [], gain)
            self.assertEqual(controls(p)['preamp'], gain)
            self.assertEqual(Profile.from_dict(p.to_dict()), p)
        p = Profile('Muted', [], -11.5, True)
        self.assertEqual(Profile.from_dict(p.to_dict()), p)
        self.assertEqual(controls(p)['mute'], 1)
        with self.assertRaises(ValueError): p.apo()
        old = {'version': 1, 'name': 'Old', 'bands': [], 'preamp': -3}
        self.assertFalse(Profile.from_dict(old).muted)
        for gain in (-97, 19):
            with self.assertRaises(ValueError): Profile('Bad', [], gain).validate()

    def test_native_controls(self):
        p = Profile('Test', [Band(80,-4,2)], -1)
        values = controls(p)
        self.assertEqual(values['b0_type'], 1)
        self.assertEqual(values['b0_gain'], -4)
        self.assertEqual(values['b7_type'], 0)
        self.assertEqual(len(values), 35)
        self.assertIn('PK Fc 80.0 Hz Gain -4.0 dB Q 2.00', p.apo())

if __name__ == '__main__': unittest.main()
