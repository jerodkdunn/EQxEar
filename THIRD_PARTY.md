# Dependencies and attribution

EQxEar's original Python, C, scripts, tests and documentation are covered by the
root Apache-2.0 LICENSE. This repository does not vendor the runtime libraries
listed below. Install them through your Linux distribution. Their licenses
remain separate from EQxEar's license. A distributor bundling those libraries
must also supply their required notices and meet their redistribution terms.

| Component | Use | Upstream license information |
| --- | --- | --- |
| Python | Application and service runtime | [PSF license and included component notices](https://docs.python.org/3/license.html) |
| PyGObject | GTK/GLib bindings | [LGPL-2.1-or-later](https://gitlab.gnome.org/GNOME/pygobject/-/blob/main/COPYING) |
| GTK and GLib | UI and desktop integration | [GTK LGPL-2.1-or-later](https://gitlab.gnome.org/GNOME/gtk/-/blob/main/COPYING), [GLib notices](https://gitlab.gnome.org/GNOME/glib/-/blob/main/COPYING) |
| Pycairo and Cairo | Graph and analyzer drawing | [Pycairo LGPL-2.1 or MPL-1.1](https://github.com/pygobject/pycairo/blob/main/COPYING), [Cairo license details](https://www.cairographics.org/license/) |
| PipeWire, including pipewire-pulse | Audio graph and Pulse protocol server | [MIT](https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/master/COPYING) |
| WirePlumber | Session management and routing policy | [MIT](https://gitlab.freedesktop.org/pipewire/wireplumber/-/blob/master/LICENSE) |
| PulseAudio client tools / libpulse | `pactl`, `pacat`, `parec` for routing, spectrum capture and test playback | [LGPL-2.1-or-later, with upstream component details](https://www.freedesktop.org/wiki/Software/PulseAudio/License/) |
| LV2 core headers | Native plugin interface, build dependency | [ISC](LICENSES/LV2.txt), [upstream](https://lv2plug.in/) |
| C compiler, C runtime and math library | Build and execute native DSP/FFT | Supplied by the platform; their licenses depend on the toolchain and distribution. No compiler or C runtime is bundled here. |

The LV2 notice is included because the plugin is compiled against its core
header. The launcher uses the desktop's `audio-card` icon; no third-party icon
asset is bundled. Omarchy theme colors are read from the user's installation.
Omarchy itself is not bundled and is not required to run the application.

## Implementation sources

DMS's [original YouTube video](https://youtu.be/WIWHINQ5lV8) and
[EQ by Ear utility](https://eqbyear.com/) inspired this project. Credit goes to
DMS for the idea that EQxEar continues as a live desktop system equalizer.
EQxEar is an independent implementation, with no copied website code or assets
and no affiliation or endorsement implied.

The native EQ and Python response calculations implement Robert Bristow-Johnson's
standard biquad equations. The [W3C Audio EQ Cookbook](https://www.w3.org/TR/audio-eq-cookbook/)
is a mathematical reference; its document text is not bundled.

`native/fft.c` is an original radix-2 FFT implementation. The current source does
not include, link to, or require FFTW. Older private development revisions used
FFTW through its installed shared library; that dependency was removed before
release. No FFTW implementation was copied into the replacement.

This inventory covers the current Linux source distribution. A future binary
bundle or macOS port needs a review of the components it actually ships.
