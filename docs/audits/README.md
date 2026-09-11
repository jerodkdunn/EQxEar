# Pre-release verification

The initial public source passed an independent Claude code audit and a focused
recheck after fixes. The final review found no code release blockers or regressions.

Validation included 62 unit tests, GTK interaction checks, isolated PipeWire EQ,
spectrum capture and crash-recovery tests, and native DSP/FFT sanitizer checks.
Additional probes verified shutdown during capture and upgrading from the earlier
background service. Tests used isolated audio services and virtual devices.

See [GitHub Actions](https://github.com/jerodkdunn/EQxEar/actions/workflows/checks.yml)
for checks on public revisions. The native sanitizer check is retained here.

Coexistence with other audio processors was not validated. The current backend
is Linux-only. This review does not establish macOS support,
trademark clearance, or App Store approval.
