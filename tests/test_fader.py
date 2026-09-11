# SPDX-License-Identifier: Apache-2.0
import unittest
from eqxear.fader import db_to_position, position_to_db

class FaderTests(unittest.TestCase):
    def test_landmarks_and_roundtrip(self):
        self.assertEqual(position_to_db(0), None)
        self.assertEqual(position_to_db(.75), 0)
        self.assertEqual(position_to_db(1), 18)
        for db in (-96, -80, -60, -36, -18, -11.5, -6, 0, 6, 18):
            self.assertAlmostEqual(position_to_db(db_to_position(db)), db)

    def test_taper_and_limits(self):
        values = [position_to_db(i/1000) for i in range(6,1001)]
        self.assertEqual(values, sorted(values))
        self.assertEqual(position_to_db(-100), None)
        self.assertEqual(position_to_db(100), 18)
        self.assertGreater(position_to_db(.12)-position_to_db(.1), position_to_db(.75)-position_to_db(.73))
