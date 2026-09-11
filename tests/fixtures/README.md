# Response and preset exchange fixtures

These fixtures are original EQxEar test data, distributed under the repository's Apache 2.0 license. They contain no copied upstream implementation or test data.

`response-cases.json` describes filters, processing rates, probe frequencies, preamp and expected response values. It covers off-center bells at multiple rates, resonant shelves, a low-rate frequency clamp, and overlapping boosts normalized at 96 kHz. Model tests and actual native plugin measurements check the same cases. Another implementation can consume these values to compare filter behavior independently of the GUI or audio backend.

`eqbyear-session.json` is an original example using EQ by Ear's version 1 session field names. It includes enabled and disabled bands and browser session metadata. Import tests verify that EQ settings are preserved and browser playback settings do not become EQ controls. `peq.txt` represents equivalent active and disabled filters in the supported Equalizer APO text syntax.

The supported PEQ subset contains `Preamp` and numbered `Filter` lines with ON/OFF, PK/LSC/HSC, Fc, Gain and Q fields. Blank lines and `#` comments are accepted. EQxEar supports eight bands, frequencies from 20 to 20,000 Hz, gains from -12 to +12 dB, Q from 0.3 to 10, and preamp from -96 to +18 dB. Import rejects unsupported directives and values rather than loading only part of a curve.

Run the fixture checks with `python3 -m unittest discover -s tests -v`. They do not require browser access or a download of another project's source.
