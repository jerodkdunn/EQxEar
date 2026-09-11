# Contributing

Bug reports should include the EQxEar commit, Linux distribution, Python, GTK,
PipeWire and WirePlumber versions, the steps to reproduce, and the expected
behavior. For audio problems, include whether bypass or another output changes
the result. Remove device identifiers or other personal details from logs before
posting them.

Keep changes focused and explain how you tested them. Run the checks in the
README. Audio integration tests must use the isolated session provided by
`tests/isolated_audio.py`; they must not redirect the contributor's desktop audio.
Use a separate session D-Bus when running them.

By intentionally submitting a contribution for inclusion, you agree to license
it under Apache-2.0, as described in section 5 of LICENSE. You retain ownership of
your contribution. Submit only material you have the right to contribute. Tell
us about copied or adapted third-party code and its license so its notices and
compatibility can be reviewed before merging. Do not add a dependency without
identifying its license and explaining why it is needed.

The current application targets Linux with PipeWire. A future macOS port may
share DSP and preset code, but its audio backend and packaging need separate
implementation and validation.
