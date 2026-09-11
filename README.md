# EQxEar

A live system equalizer for Omarchy, with its own native PipeWire DSP. Play music, drag an EQ point or move the preamp fader, and hear the change immediately.

EQxEar was inspired by DMS's [original YouTube video](https://youtu.be/WIWHINQ5lV8) and [EQ by Ear utility](https://eqbyear.com/). Credit goes to DMS for the idea that started this project. EQxEar continues that idea as a desktop application with live system-wide EQ, saved presets, and controls for tuning while listening. Thank you, DMS, for sharing it.

## Run

Clone the source:

```sh
git clone https://github.com/jerodkdunn/EQxEar.git
cd EQxEar
```

Run `./run`, or run `./install-launcher` once and open **EQxEar** from the desktop launcher. The first launch compiles the small native plugin into your user cache. Opening the RTA also builds its native FFT there. Dependencies are Python 3.11 or newer, PyGObject, Pycairo, GTK 4, PipeWire with filter-chain/LV2 support, pipewire-pulse, WirePlumber, a C compiler, the LV2 headers, and `pactl`, `pacat`, and `parec` from libpulse. Python 3.11 is required for `tomllib`. Install the bindings through your distribution rather than a separate virtual environment.

On Arch Linux or Omarchy, install missing dependencies with:

```sh
sudo pacman -S --needed python python-gobject python-cairo gtk4 pipewire pipewire-audio pipewire-pulse wireplumber libpulse base-devel lv2
```

Use a normal desktop user session with PipeWire, pipewire-pulse and WirePlumber running. No root access is needed to run EQxEar or install its per-user launcher. Run `./install-launcher` again if you move the checkout.

The validated desktop stack is Omarchy/Arch Linux with Python 3.14.7, GTK 4.22.4, PipeWire 1.6.8 and WirePlumber 0.5.17. These are tested versions, not established minimum versions for GTK or PipeWire. Other distributions have not yet been validated. The CI workflow uses an Arch container and records its package versions; it runs audio against isolated virtual devices. Its audio-session configuration uses WirePlumber 0.5 syntax. Omarchy supplies optional theme colors; another GTK desktop can use the fallback theme.

Opening the app starts its background engine with the loaded curve. If the engine is already running, reopening the app reconnects to its current curve and output.

## Listening

- The engine creates the **EQxEar** virtual output and makes it the default for desktop playback. It moves existing ordinary desktop playback streams into that output. The processed signal goes to the speakers or headphones selected in the app. Choose **Use output** to change that physical destination.
- Graph dragging, numeric band edits, band type/enabled changes, preamp dragging, normalization, Undo, and preset recall all update the running engine. There is no Apply button. Updates are coalesced at approximately 30 per second; the native DSP ramps parameter changes over 10 ms. End-to-end audible latency also depends on the device and PipeWire graph.
- **Normalize peak to 0 dB** compensates for the combined response with the preamp. A single +11.5 dB bell at 29 Hz with Q 0.3 gives -11.5 dB preamp. Raising the fader afterward adds makeup gain and can put peaks above zero again.
- The fader has a console-style logarithmic amplitude taper, 0 dB unity, +18 dB at the top, and true mute at the bottom. Numeric entry and +/− buttons support 0.1 dB steps. Enter `-inf` to mute.
- **Bypass EQ** smoothly bypasses the filters and preamp, including mute, for comparison. Editing while bypassed keeps bypass engaged until you enable EQ again.
- **Save as…** stores a preset; **Recall** makes it audible immediately. **Delete…** removes the selected saved preset after confirmation and leaves the current EQ playing. **Copy EQ** exports Equalizer APO text. Each graph/fader drag is one Undo step.
- Drag an EQ point horizontally to change frequency and vertically to change gain. Click a point and scroll up to narrow its Q, or down to widen it. Numeric controls stay synchronized.
- The spectrum analyzer shows 31 logarithmically spaced output-monitor bars, calibrated in dBFS. Enable **RTA behind EQ graph** for a low-contrast overlay; its height uses the analyzer’s -90 to 0 dBFS range independently of the EQ gain axis. Both views share one capture stream. Capture stops when both views are hidden, and closing the UI stops the analyzer.
- Collapse panels with their caret buttons. Drag the dotted header handle to reorder, or focus it and press Alt+Up/Down. Order, collapsed state, and overlay visibility are saved in `ui.json`. Collapse the other panels to use the RTA in a small window.
- Tone tuning remains available under the collapsed **Optional tone tuning** panel. It is not needed to tune while listening to music.

Closing the window leaves system EQ running. **Stop system EQ** smoothly disables processing while retaining the same virtual playback device in transparent passthrough. This avoids pausing media when its output device disappears. Starting EQ again reuses that device. The **Quit background service** button in **Output & presets** removes the virtual device and restores playback to the previous physical output. The editor stays open; choose **Start system EQ** to reconnect. Use this action before restarting the app after an upgrade so the new backend code can load. The app does not enable login autostart. Start it again after logging in.

If the background service crashes or is killed, its native audio child exits too. The service checks that its audio graph and playback device still exist and reports a disconnected state when they disappear. After PipeWire and its session manager are available again, choose **Start system EQ** to reconnect. An unmanaged EQxEar device left by an older version causes an explicit refusal to start; stop the older instance first. EQxEar does not take control of audio nodes it does not own.

The engine currently processes stereo desktop playback. Applications using direct ALSA, exclusive device access, or explicitly pinned native PipeWire links may bypass the default output. Microphone processing is outside EQxEar's scope. A disconnected selected output is reported in the status line; select another available output to continue.

## Storage

EQxEar retains its existing data directory at `$XDG_DATA_HOME/eqxear-v2`, normally `~/.local/share/eqxear-v2`, so existing presets and layouts remain available after the rename. On first launch, a valid v1 preset library and draft are copied into the new library if available. Subsequent edits do not modify v1 data. Presets and drafts use atomic file replacement.

The background service uses a private socket under `$XDG_RUNTIME_DIR/eqxear-v2`. Its audio configuration and diagnostic logs are there too. The native plugin is built under `$XDG_CACHE_HOME/eqxear-v2`, normally `~/.cache/eqxear-v2`. No PipeWire, Hyprland, or Omarchy system configuration is edited. The UI follows the active Omarchy theme.

## Audio engine

`native/eq.c` implements eight stereo RBJ bell/shelf biquads plus preamp gain, mute, and bypass. PipeWire loads it through its filter-chain module as a private LV2 plugin. All audio buffers and filter state are allocated before processing; the audio callback uses no locks, filesystem access, or allocation. Audio runs outside the Python UI. Controls change on the existing node without restarting the stream.

The DSP calculates coefficients at its actual sample rate. The graph and peak-normalization preview use 48 kHz. At lower sample rates, the DSP clamps frequencies above 49% of the sample rate. Normalization is a steady-state filter-response adjustment, not a true-peak limiter.

The RTA uses an original radix-2 FFT in `native/fft.c`, with no external FFT library dependency. It retains Hann windowing, stereo power averaging, and calibrated dBFS readings.

## Validation

```
python3 -m unittest discover -s tests -v
python3 tests/ui_smoke.py
dbus-run-session -- python3 tests/integration_v2.py
dbus-run-session -- python3 tests/integration_spectrum.py
dbus-run-session -- python3 tests/integration_recovery.py
```

Native tests run the compiled plugin directly and check stereo isolation, live gain, normalization, mute, bypass, shelf responses, sample rates, and extreme control transitions. The GTK check verifies interactions and live-update scheduling without changing desktop audio. On a headless machine, run it with `xvfb-run -a dbus-run-session -- python3 tests/ui_smoke.py`. The launcher test uses GLib to launch a temporary stub application and also runs `desktop-file-validate` when installed.

The integration test starts separate PipeWire, Pulse, and WirePlumber processes with hardware monitors disabled. It sends stereo audio through the actual EQxEar sink into a virtual speaker monitor, measures gain changes without restarting the node, and checks normalization, mute/unmute, bypass, output switching, routing restoration, stop/start without removing or corking the playback stream, and background-service reconnection.

The recovery integration test checks service death, audio-graph loss and restart behavior in the same isolated environment.

## References

EQxEar is an independent implementation and is not affiliated with or endorsed by DMS. The native engine uses standard RBJ biquad equations and PipeWire's documented [filter-chain module](https://docs.pipewire.org/page_module_filter_chain.html).

## License and contributions

EQxEar is licensed under [Apache-2.0](LICENSE). See [NOTICE](NOTICE),
[dependency and attribution details](THIRD_PARTY.md), and
[contribution guidelines](CONTRIBUTING.md). The license permits use, modification
and commercial distribution subject to its terms. A macOS version is a possible
future project; the current audio backend is Linux-only.
