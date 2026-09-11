# SPDX-License-Identifier: Apache-2.0
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import tomllib
import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, GLib, Gio
from .model import Band, Profile, Library, KINDS, from_marks, atomic_json
from .engine import Engine, Tone, outputs
from .fader import LevelFader
from .spectrum import Analyzer, draw_bars
from .panels import Panel
from .importers import import_profile, MAX_IMPORT_BYTES


def label(text, style=None):
    w = Gtk.Label(label=text, xalign=0)
    if style:
        w.add_css_class(style)
    return w


def button(text, callback, style=None):
    w = Gtk.Button(label=text)
    w.set_valign(Gtk.Align.CENTER)
    w.set_size_request(-1, 38)
    w.connect('clicked', callback)
    if style:
        w.add_css_class(style)
    return w


def box(vertical=False, spacing=10):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, spacing=spacing)


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='EQxEar')
        self.set_default_size(1120, 800)
        self.library, self.engine, self.tone = Library(), Engine(), Tone()
        self.load_error = None
        try:
            self.profiles = self.library.load()
        except (ValueError, KeyError, TypeError, OSError) as e:
            self.profiles = [Profile('Flat', [])]
            self.load_error = f'Could not read your library: {e}. Original file left intact.'
        self.profile = copy.deepcopy(self.profiles[0]) if self.profiles else Profile('Flat', [])
        self.draft_path = self.library.path.with_name('draft.json')
        self.draft_error = None
        if self.draft_path.exists():
            try:
                self.profile = Profile.from_dict(json.loads(self.draft_path.read_text()))
            except (ValueError, KeyError, TypeError, OSError) as e:
                self.draft_error = f'Could not read your draft: {e}. Original file left intact; fix or move it to resume draft saving.'
        self.sample_rate = 48000
        self.sample_rate_known = False
        self.marks = {}
        self.history = []
        self.dragging = None
        self.selected_band = None
        self.fader_before = None
        self.graph_limits = None
        self.syncing_gain = False
        self.busy = False
        self.pending_actions = []
        self.analyzer = Analyzer()
        self.monitor_output = None
        self.ui_path = self.library.path.with_name("ui.json")
        try:
            self.ui = json.loads(self.ui_path.read_text())
            if not isinstance(self.ui, dict): self.ui = {}
        except (OSError, ValueError):
            self.ui = {}
        self.panels = {}
        self.dirty = False
        self.live_timer = 0
        self.connected_once = False
        self.active = False
        self.applied = None
        self.closed = False
        self.theme_text = None
        self.provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.load_theme()
        root = box(True, 18)
        for edge in ('top', 'bottom', 'start', 'end'):
            getattr(root, 'set_margin_'+edge)(24)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(root)
        self.set_child(scroll)
        header = box()
        brand = box(True, 3)
        brand.append(label('EQxEar', 'title'))
        brand.append(label('Live system equalizer', 'muted'))
        brand.set_hexpand(True)
        header.append(brand)
        self.bypass_button = button('Bypass EQ', self.toggle_bypass)
        self.bypass_button.set_sensitive(False)
        header.append(self.bypass_button)
        self.apply_button = button('Start system EQ', self.toggle_engine, 'suggested-action')
        self.apply_button.set_tooltip_text('Start or stop EQ processing. Stopping keeps a transparent playback device connected so media keeps playing.')
        header.append(self.apply_button)
        root.append(header)
        self.panel_box = box(True, 12)
        root.append(self.panel_box)
        controls = box(True, 10)
        equalizer = box(True, 10)
        row = box()
        row.append(label('OUTPUT', 'muted'))
        self.device = Gtk.DropDown.new_from_strings(['Loading outputs…'])
        self.device.set_hexpand(True)
        self.devices = []
        row.append(self.device)
        row.append(button('Use output', self.set_output))
        row.append(button('Refresh', lambda *_: self.refresh_outputs()))
        controls.append(row)
        row = box()
        row.append(label('PRESET', 'muted'))
        self.preset = Gtk.DropDown.new_from_strings([p.name for p in self.profiles])
        self.preset.set_selected(next((i for i,p in enumerate(self.profiles) if p.name == self.profile.name), 0))
        self.preset.set_hexpand(True)
        row.append(self.preset)
        self.recall_button = button('Recall', self.recall)
        row.append(self.recall_button)
        row.append(button('Save as…', self.save_dialog))
        row.append(button('Import…', self.import_dialog))
        self.delete_button = button('Delete…', self.delete_preset_dialog)
        self.delete_button.set_tooltip_text('Delete the selected saved preset. The current EQ keeps playing.')
        row.append(self.delete_button)
        self.preset.connect('notify::selected', lambda *_: self.update_preset_buttons())
        self.update_preset_buttons()
        row.append(button('Copy EQ', self.copy_eq))
        controls.append(row)
        row = box()
        self.quit_service_button = button('Quit background service', self.quit_service)
        self.quit_service_button.set_tooltip_text('Remove the EQ playback device and restore the previous physical output. Closing this window alone leaves system EQ running.')
        row.append(self.quit_service_button)
        controls.append(row)
        self.graph = Gtk.DrawingArea(content_height=240, hexpand=True)
        self.graph.set_draw_func(self.draw_graph)
        self.graph.set_tooltip_text('Drag a point horizontally for frequency and vertically for gain. Select a point, then scroll for Q.')
        drag = Gtk.GestureDrag.new()
        drag.set_button(1)
        drag.connect('drag-begin', self.graph_drag_begin)
        drag.connect('drag-update', self.graph_drag_update)
        drag.connect('drag-end', self.graph_drag_end)
        drag.connect('cancel', lambda *_: self.graph_drag_end(None, 0, 0))
        self.graph.add_controller(drag)
        motion = Gtk.EventControllerMotion.new()
        motion.connect('motion', self.graph_motion)
        motion.connect('leave', lambda *_: self.graph.set_cursor_from_name('default') if self.dragging is None else None)
        self.graph.add_controller(motion)
        wheel = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect("scroll", self.graph_scroll)
        self.graph.add_controller(wheel)
        graph_row = box(False, 18)
        graph_row.append(self.graph)
        self.preamp = LevelFader(self.preamp_changed, self.fader_begin, self.fader_end,
                                 lambda: (self.ink, self.muted, self.accent))
        self.sync_preamp()
        graph_row.append(self.preamp)
        self.overlay = Gtk.CheckButton(label="RTA behind EQ graph")
        self.overlay.set_active(bool(self.ui.get("overlay", False)))
        self.overlay.connect("toggled", lambda *_: self.layout_changed())
        equalizer.append(self.overlay)
        equalizer.append(graph_row)
        self.rate_notice = label('', 'muted')
        self.rate_notice.set_wrap(True)
        self.rate_notice.set_visible(False)
        equalizer.append(self.rate_notice)
        row = box()
        row.append(label('PARAMETRIC EQ', 'section'))
        spacer = label(''); spacer.set_hexpand(True); row.append(spacer)
        normalize = button('Normalize peak to 0 dB', self.normalize_peak)
        normalize.set_tooltip_text('Lower the entire response using the preamp, accounting for overlapping filters. Changes are live.')
        row.append(normalize)
        row.append(button('+ Band', self.add_band))
        row.append(button('Undo', self.undo))
        equalizer.append(row)
        self.band_box = box(True, 5)
        equalizer.append(self.band_box)
        self.render_bands()
        tune = box(True, 12)
        tune.add_css_class('panel')
        row = box()
        row.append(label('TUNE BY EAR', 'section'))
        self.frequency_label = label('1,000 Hz', 'frequency')
        self.frequency_label.set_hexpand(True)
        self.frequency_label.set_xalign(1)
        row.append(self.frequency_label)
        self.play = button('Play tone', self.toggle_tone)
        row.append(self.play)
        tune.append(row)
        tune.append(label('Start at a low device volume. Sweep slowly and listen for a local peak or dip.', 'muted'))
        self.frequency = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, .001)
        self.frequency.set_draw_value(False)
        self.frequency.set_value(math.log(1000/20)/math.log(1000))
        self.frequency.connect('value-changed', self.frequency_changed)
        for f in (20, 100, 1000, 10000, 20000):
            self.frequency.add_mark(math.log(f/20)/math.log(1000), Gtk.PositionType.BOTTOM, f'{f/1000:g}k' if f >= 1000 else str(f))
        tune.append(self.frequency)
        row = box()
        row.append(label('Tone level'))
        volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -60, -18, 1)
        volume.set_hexpand(True); volume.set_value(-42); volume.set_digits(0)
        volume.connect('value-changed', lambda w: setattr(self.tone, 'level', w.get_value()))
        row.append(volume); row.append(label('dBFS', 'muted'))
        tune.append(row)
        row = box()
        self.mark_buttons = {}
        for name in ('start', 'end'):
            w = button('Mark '+name, lambda _, n=name: self.mark(n))
            self.mark_buttons[name] = w
            row.append(w)
        self.dip = Gtk.CheckButton(label='Heard a dip')
        row.append(self.dip)
        row.append(button('Create correction', self.create_correction))
        tune.append(row)
        tune.append(label('Mark the two edges of an uneven region. The correction is centered halfway between them on the frequency scale. Adjust its gain by listening.', 'muted'))
        spectrum = box(True, 6)
        self.rta = Gtk.DrawingArea(content_height=200, hexpand=True)
        self.rta.set_draw_func(self.draw_rta)
        spectrum.append(self.rta)
        self.rta_status = label('Output spectrum · dBFS', 'muted')
        spectrum.append(self.rta_status)
        definitions = [('controls', 'Output & presets', controls, True),
                       ('eq', 'Equalizer', equalizer, True),
                       ('rta', 'Spectrum analyzer', spectrum, True),
                       ('tone', 'Optional tone tuning', tune, False)]
        expanded = self.ui.get('expanded', {})
        if not isinstance(expanded, dict): expanded = {}
        for key, title, content, default in definitions:
            panel = Panel(key, title, content, bool(expanded.get(key, default)), self.layout_changed, self.move_panel)
            target = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
            target.connect('drop', lambda _, value, x, y, p=panel: self.drop_panel(value, p.key, y > p.get_height()/2))
            panel.add_controller(target)
            self.panels[key] = panel
        order = self.ui.get('order', [])
        if not isinstance(order, list): order = []
        self.panel_order = []
        for key in order + list(self.panels):
            if isinstance(key, str) and key in self.panels and key not in self.panel_order:
                self.panel_order.append(key)
                self.panel_box.append(self.panels[key])
        self.status = label(self.load_error or self.draft_error or 'Connecting to native system EQ…', 'muted')
        self.status.set_wrap(True)
        root.append(self.status)
        self.connect('close-request', self.close)
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self.key_pressed)
        self.add_controller(keys)
        self.refresh_outputs()
        GLib.timeout_add_seconds(2, self.tick)
        GLib.timeout_add(40, self.spectrum_tick)

    def load_theme(self):
        path = Path(os.environ.get('XDG_STATE_HOME', Path.home()/'.local/state'))/'omarchy/current/theme/colors.toml'
        try:
            raw = path.read_text()
            colors = tomllib.loads(raw)
        except (OSError, ValueError):
            raw, colors = '', {}
        if raw == self.theme_text:
            return
        self.theme_text = raw
        bg = colors.get('background', '#111316'); fg = colors.get('foreground', '#e5e7eb')
        panel = colors.get('lighter_background', '#202329'); muted = colors.get('muted', '#9ca3af')
        accent = colors.get('accent', '#91b8a1')
        # Some themes use nearly invisible muted text. Blend toward foreground.
        import re
        def valid(value, fallback):
            return value if isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value) else fallback
        bg, fg, panel, muted, accent = [valid(v, d) for v, d in zip((bg,fg,panel,muted,accent), ('#111316','#e5e7eb','#202329','#9ca3af','#91b8a1'))]
        def luminance(value):
            channels = [int(value[i:i+2],16)/255 for i in (1,3,5)]
            linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in channels]
            return sum(v*w for v,w in zip(linear,(.2126,.7152,.0722)))
        for _ in range(20):
            a,b = sorted((luminance(muted),luminance(bg)))
            if (b+.05)/(a+.05) >= 4.5: break
            muted = '#' + ''.join(f'{round(int(muted[i:i+2],16)*.8+int(fg[i:i+2],16)*.2):02x}' for i in (1,3,5))
        self.ink, self.accent, self.muted = fg, accent, muted
        self.provider.load_from_data(f'''
window {{ background: {bg}; color: {fg}; font-family: sans-serif; }}
.title {{ font-size: 30px; font-weight: 700; letter-spacing: -1px; }}
.muted {{ color: {muted}; font-size: 12px; }}
.section {{ font-size: 12px; font-weight: 700; letter-spacing: 1px; }}
.frequency {{ font-family: monospace; font-size: 22px; }}
button {{ color: {fg}; background: {panel}; border-radius: 5px; padding: 7px 12px; box-shadow: none; }}
button.suggested-action {{ background: {fg}; color: {bg}; }}
.panel {{ background: {panel}; border-radius: 8px; padding: 16px; }}
.band {{ background: {panel}; border-radius: 5px; padding: 5px 9px; }}
spinbutton, dropdown {{ color: {fg}; background: {panel}; border-radius: 5px; }}
scale highlight {{ background: {accent}; }}
'''.encode())

    def run_work(self, fn, done, pending=None, key=None):
        # Polls/live updates can wait for their next tick. Explicit user actions
        # are retained; repeated output/refresh requests keep the latest intent.
        if self.busy:
            if pending or key:
                action_key = key or 'transport'
                self.pending_actions = [a for a in self.pending_actions if a[3] != action_key]
                self.pending_actions.append((fn, done, pending, action_key))
                if pending:
                    self.apply_button.set_label(pending)
                    self.apply_button.set_sensitive(False)
                    self.bypass_button.set_sensitive(False)
                    self.quit_service_button.set_sensitive(False)
            return
        self.busy = True
        if pending:
            self.apply_button.set_label(pending)
            self.apply_button.set_sensitive(False)
            self.bypass_button.set_sensitive(False)
            self.quit_service_button.set_sensitive(False)
        def work():
            try:
                result, error = fn(), None
            except Exception as e:
                result, error = None, str(e)
            GLib.idle_add(finish, result, error)
        def finish(result, error):
            if self.closed:
                self.busy = False
                self.pending_actions.clear()
                return False
            try:
                if error:
                    raise RuntimeError(error)
                done(result)
            except Exception as e:
                self.status.set_text(f'Operation failed: {e}')
            finally:
                self.busy = False
                self.apply_button.set_label("Stop system EQ" if self.engine.owned else "Start system EQ")
                self.apply_button.set_sensitive(True)
                self.bypass_button.set_sensitive(self.engine.owned)
                self.quit_service_button.set_sensitive(True)
                if self.pending_actions:
                    self.run_work(*self.pending_actions.pop(0))
            return False
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def validate_engine_state(state):
        if not isinstance(state, dict):
            raise ValueError('Invalid engine response: expected an object')
        for key in ('running', 'connected', 'bypassed'):
            if key in state and type(state[key]) is not bool:
                raise ValueError(f'Invalid engine response: {key} must be boolean')
        for key in ('output', 'warning'):
            if state.get(key) is not None and not isinstance(state[key], str):
                raise ValueError(f'Invalid engine response: {key} must be text')
        return state

    def refresh_outputs(self):
        def work():
            return outputs(), self.engine.status()
        def done(result):
            devices, state = result
            self.validate_engine_state(state)
            if not isinstance(devices, list) or any(not isinstance(d, (list, tuple)) or len(d) != 2 or any(not isinstance(v, str) for v in d) for d in devices):
                raise ValueError('Invalid audio output list')
            profile = Profile.from_dict(state['profile']) if not self.connected_once and state.get('running') else None
            self.devices = devices
            self.device.set_model(Gtk.StringList.new([d[1] for d in devices]))
            if state.get('running') or state.get('connected'):
                for i, device in enumerate(devices):
                    if device[0] == state.get('output'): self.device.set_selected(i)
                if profile is not None:
                    self.profile = profile
                    self.preset.set_selected(next((i for i,p in enumerate(self.profiles) if p.name == self.profile.name), 0))
                    self.sync_preamp(); self.render_bands(); self.graph.queue_draw()
                self.show_engine_state(state)
            elif not self.connected_once and devices:
                self.start_engine()
            self.connected_once = True
        self.run_work(work, done, key='refresh')

    def show_engine_state(self, state):
        self.validate_engine_state(state)
        rate = state.get('sample_rate')
        known = bool(state.get('connected') and type(rate) in (int, float)
                     and 1000 <= rate <= 768000)
        rate = float(rate) if known else 48000
        if (rate, known) != (self.sample_rate, self.sample_rate_known):
            self.sample_rate, self.sample_rate_known = rate, known
            self.graph_limits = None
            self.graph.queue_draw()
        limited = self.sample_rate*.49 < 20000
        self.rate_notice.set_visible(limited)
        if limited:
            self.rate_notice.set_text(f'At {self.sample_rate/1000:g} kHz, higher band frequencies use the {self.sample_rate*.49/1000:g} kHz DSP limit. Stored frequencies stay unchanged; the graph holds the Nyquist response above {self.sample_rate/2000:g} kHz.')
        if state.get('output'):
            self.monitor_output = state['output']
        running = state.get('running', False)
        self.engine.owned = running
        self.active = running and not state.get('bypassed', False)
        self.apply_button.set_label('Stop system EQ' if running else 'Start system EQ')
        self.bypass_button.set_sensitive(running)
        self.bypass_button.set_label('Bypass EQ' if self.active else 'Enable EQ')
        self.status.set_text(state.get('warning') or ('Live system EQ' if self.active else 'EQ bypassed' if running else 'EQ stopped · audio passes through unchanged' if state.get('connected') else 'System EQ stopped'))

    def start_engine(self):
        i = self.device.get_selected()
        if i >= len(self.devices):
            self.status.set_text('Connect an audio output first.'); return
        output = self.devices[i][0]
        profile = copy.deepcopy(self.profile)
        self.status.set_text('Starting native PipeWire EQ…')
        self.run_work(lambda: self.engine.start(profile, output), self.started, "Starting…")

    def started(self, state):
        self.show_engine_state(state)
        self.schedule_live()

    def toggle_engine(self, *_):
        if self.engine.owned:
            self.run_work(self.engine.stop, self.show_engine_state, "Stopping…")
        else:
            self.start_engine()

    def quit_service(self, *_):
        # Explicit quit wins over queued Start/Refresh work, including the
        # first-open refresh callback's automatic startup path.
        self.connected_once = True
        self.pending_actions.clear()
        if self.live_timer:
            GLib.source_remove(self.live_timer)
            self.live_timer = 0
        def disconnected(state):
            self.validate_engine_state(state)
            if state.get('running') or state.get('connected'):
                raise RuntimeError('The background service did not disconnect.')
            self.show_engine_state(state)
            self.status.set_text(state.get('warning') or 'Background service quit. Start system EQ to reconnect.')
        self.run_work(self.engine.disconnect, disconnected, 'Quitting…', key='disconnect')

    def set_output(self, *_):
        i = self.device.get_selected()
        if i >= len(self.devices): return
        name, description = self.devices[i]
        if self.engine.owned:
            self.run_work(lambda: self.engine.select_output(name), self.show_engine_state, key='output')
        else:
            self.status.set_text(f'Selected {description}. Start system EQ to listen.')

    def schedule_live(self):
        if self.engine.owned and not self.live_timer and not self.closed:
            self.live_timer = GLib.timeout_add(33, self.flush_live)

    def flush_live(self):
        if self.closed or not self.engine.owned:
            self.live_timer = 0; return False
        if self.busy: return True
        self.live_timer = 0
        profile = copy.deepcopy(self.profile)
        self.run_work(lambda: self.engine.apply(profile), self.show_engine_state)
        return False

    def remember(self):
        self.history.append(copy.deepcopy(self.profile))
        self.history = self.history[-40:]

    def save_draft(self):
        if self.draft_error and self.draft_path.exists():
            try:
                Profile.from_dict(json.loads(self.draft_path.read_text()))
            except (ValueError, OSError) as e:
                raise ValueError(self.draft_error) from e
        self.draft_error = None
        atomic_json(self.draft_path, self.profile.to_dict())

    def changed(self):
        self.dirty = True
        try:
            self.save_draft()
        except (ValueError, OSError) as e:
            self.status.set_text(f'Could not save draft: {e}')
            self.graph.queue_draw()
            self.schedule_live()
            return
        self.graph.queue_draw()
        self.schedule_live()
        self.status.set_text('Live edits. Save to keep this preset.' if self.engine.owned else 'Edited. Start system EQ to listen.')

    def sync_preamp(self):
        self.preamp.set_value(None if self.profile.muted else self.profile.preamp)

    def preamp_changed(self, value):
        muted = value is None
        gain = self.profile.preamp if muted else value
        if (gain, muted) == (self.profile.preamp, self.profile.muted):
            return
        if self.fader_before is None:
            self.remember()
        self.profile.preamp, self.profile.muted = gain, muted
        self.sync_preamp()
        if self.fader_before is None:
            self.changed()
        else:
            self.graph.queue_draw()
        self.schedule_live()
        self.status.set_text('Preamp muted.' if muted else f'Preamp {gain:+.1f} dB')

    def fader_begin(self):
        self.fader_before = copy.deepcopy(self.profile)

    def fader_end(self):
        before, self.fader_before = self.fader_before, None
        if before is not None and before != self.profile:
            self.history.append(before)
            self.history = self.history[-40:]
            self.changed()

    def normalize_peak(self, *_):
        try:
            self.preamp_changed(self.profile.normalization_preamp(rate=self.sample_rate))
            context = f'{self.sample_rate/1000:g} kHz engine' if self.sample_rate_known else '48 kHz preview (engine rate unavailable)'
            self.status.set_text(f'Preamp set to {self.profile.preamp:.1f} dB; peak normalized for the {context}.')
        except ValueError as e:
            self.status.set_text(str(e))

    def render_bands(self):
        self.gain_spins = []
        self.band_spins = []
        self.selected_band = None
        child = self.band_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.band_box.remove(child)
            child = next_child
        if not self.profile.bands:
            self.band_box.append(label('Flat response. Add a band, or create a correction with the tuning controls below.', 'muted'))
        for i, band in enumerate(self.profile.bands):
            spins = {}
            self.band_spins.append(spins)
            row = box(False, 8); row.add_css_class('band')
            enabled = Gtk.CheckButton(label=f'{i+1:02}')
            enabled.set_active(band.enabled)
            enabled.connect('toggled', lambda w, b=band: self.edit_band(b, 'enabled', w.get_active()))
            row.append(enabled)
            kind = Gtk.DropDown.new_from_strings(list(KINDS))
            kind.set_selected(KINDS.index(band.kind))
            kind.connect('notify::selected', lambda w, _, b=band: self.edit_band(b, 'kind', KINDS[w.get_selected()]))
            row.append(kind)
            for field, low, high, step, digits, unit in [('frequency',20,20000,1,0,'Hz'), ('gain',-12,12,.1,1,'dB'), ('q',.3,10,.05,2,'Q')]:
                spin = Gtk.SpinButton.new_with_range(low, high, step)
                spin.set_digits(digits); spin.set_value(getattr(band, field)); spin.set_hexpand(True)
                spin.set_tooltip_text(field.capitalize())
                spin.connect('value-changed', lambda w, b=band, f=field: self.edit_band(b, f, w.get_value()))
                spins[field] = spin
                if field == 'gain':
                    self.gain_spins.append(spin)
                row.append(spin); row.append(label(unit, 'muted'))
            row.append(button('Remove', lambda _, n=i: self.remove_band(n)))
            self.band_box.append(row)

    def edit_band(self, band, field, value):
        if self.syncing_gain:
            return
        self.remember()
        setattr(band, field, value)
        self.changed()

    def add_band(self, *_):
        if len(self.profile.bands) >= 8:
            self.status.set_text('Eight bands maximum. Remove a band before adding another.')
            return
        self.remember(); self.profile.bands.append(Band(self.tone.frequency))
        self.render_bands(); self.changed()

    def remove_band(self, i):
        self.remember(); self.profile.bands.pop(i); self.render_bands(); self.changed()

    def undo(self, *_):
        if self.history:
            self.profile = self.history.pop()
            self.sync_preamp()
            self.render_bands(); self.changed()

    def recall(self, *_):
        i = self.preset.get_selected()
        if i < len(self.profiles):
            self.remember(); self.profile = copy.deepcopy(self.profiles[i])
            self.sync_preamp()
            self.render_bands(); self.changed()
            self.status.set_text(f'Recalled {self.profile.name}. Undo restores your previous edits.')

    def update_preset_buttons(self):
        selected = self.preset.get_selected() < len(self.profiles)
        self.recall_button.set_sensitive(selected)
        self.delete_button.set_sensitive(selected and not self.load_error)

    def delete_preset(self, index):
        if self.load_error:
            self.status.set_text(self.load_error+' Fix or move the library file before deleting presets.')
            return False
        if not 0 <= index < len(self.profiles):
            return False
        deleted = self.profiles[index]
        profiles = self.profiles[:index] + self.profiles[index+1:]
        try:
            self.library.save(profiles)
        except (ValueError, OSError) as error:
            self.status.set_text(f'Could not delete preset: {error}')
            return False
        self.profiles = profiles
        self.preset.set_model(Gtk.StringList.new([p.name for p in profiles]))
        if profiles:
            self.preset.set_selected(min(index, len(profiles)-1))
        self.update_preset_buttons()
        if deleted.name == self.profile.name:
            self.dirty = True
        self.status.set_text(f'Deleted {deleted.name}. Current EQ retained; use Save as… to save it again.')
        return True

    def delete_preset_dialog(self, *_):
        index = self.preset.get_selected()
        if index >= len(self.profiles) or self.load_error:
            return
        dialog = Gtk.Window(title='Delete preset', transient_for=self, modal=True, default_width=360)
        content = box(True, 12)
        for edge in ('top', 'bottom', 'start', 'end'):
            getattr(content, 'set_margin_'+edge)(20)
        title = label(f'Delete “{self.profiles[index].name}”?')
        title.set_wrap(True)
        content.append(title)
        info = label('This removes the saved preset. Your current EQ and playback stay as they are.', 'muted')
        info.set_wrap(True)
        content.append(info)
        actions = box()
        cancel = button('Cancel', lambda *_: dialog.close())
        actions.append(cancel)
        def remove(*_):
            if self.delete_preset(index):
                dialog.close()
            else:
                info.set_text(self.status.get_text())
        actions.append(button('Delete preset', remove, 'destructive-action'))
        content.append(actions)
        dialog.set_child(content)
        dialog.present()
        cancel.grab_focus()

    def save_dialog(self, *_):
        if self.load_error:
            self.status.set_text(self.load_error+' Fix or move the library file before saving.')
            return
        dialog = Gtk.Window(title='Save preset', transient_for=self, modal=True, default_width=360)
        content = box(True, 12)
        for edge in ('top','bottom','start','end'):
            getattr(content, 'set_margin_'+edge)(20)
        content.append(label('Preset name'))
        entry = Gtk.Entry(text=self.profile.name)
        content.append(entry)
        info = label('Use a new name to save a separate preset.', 'muted'); info.set_wrap(True); content.append(info)
        def save(*_):
            try:
                profile = copy.deepcopy(self.profile); profile.name = entry.get_text().strip(); profile.validate()
                profiles = [copy.deepcopy(p) for p in self.profiles]
                index = next((i for i,p in enumerate(profiles) if p.name == profile.name), len(profiles))
                if index == len(profiles): profiles.append(profile)
                else: profiles[index] = profile
                self.library.save(profiles)
                self.profiles = profiles; self.profile.name = profile.name; self.dirty = False
                draft_warning = None
                try:
                    self.save_draft()
                except (ValueError, OSError) as e:
                    draft_warning = f' Draft could not be saved: {e}'
                self.preset.set_model(Gtk.StringList.new([p.name for p in profiles])); self.preset.set_selected(index)
                self.update_preset_buttons()
                self.status.set_text(f'Saved {profile.name}.' + (draft_warning or '')); dialog.close()
            except (ValueError, OSError) as e:
                info.set_text(str(e))
        entry.connect('activate', save)
        actions = box()
        actions.append(button('Cancel', lambda *_: dialog.close()))
        actions.append(button('Save / replace named preset', save, 'suggested-action'))
        content.append(actions)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        def dismiss(_, key, code, state):
            if key == Gdk.KEY_Escape:
                dialog.close()
                return True
            return False
        keys.connect('key-pressed', dismiss)
        dialog.add_controller(keys)
        dialog.set_child(content); dialog.present()
        entry.grab_focus()
        entry.select_region(0, -1)

    def import_dialog(self, *_):
        dialog = Gtk.Window(title='Import EQ', transient_for=self, modal=True,
                            default_width=640, default_height=580)
        content = box(True, 10)
        for edge in ('top', 'bottom', 'start', 'end'):
            getattr(content, 'set_margin_'+edge)(20)
        description = label('Paste Equalizer APO text or EQ session JSON, or open a file. Review the curve before applying it.', 'muted')
        description.set_wrap(True)
        content.append(description)
        content.append(label('Preset name'))
        name = Gtk.Entry(placeholder_text='Use the imported name')
        content.append(name)
        source = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        source_scroll = Gtk.ScrolledWindow(min_content_height=140, vexpand=True)
        source_scroll.set_child(source)
        content.append(source_scroll)
        review = label('No curve reviewed yet.', 'muted')
        review.set_wrap(True)
        review.set_selectable(True)
        review_scroll = Gtk.ScrolledWindow(min_content_height=110, vexpand=True)
        review_scroll.set_child(review)
        content.append(review_scroll)
        candidate = None
        dialog.file_chooser = None

        def invalidate(*_):
            nonlocal candidate
            candidate = None
            accept.set_sensitive(False)
            review.set_text('Review the edited input before applying it.')

        def preview(*_):
            nonlocal candidate
            candidate = None
            accept.set_sensitive(False)
            buffer = source.get_buffer()
            try:
                profile = import_profile(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True),
                                         name=name.get_text().strip() or 'Imported EQ')
                if name.get_text().strip():
                    profile.name = name.get_text().strip()
                profile.validate()
                name.set_text(profile.name)
                lines = [profile.name, f'Preamp: {profile.preamp:+.1f} dB' + (' · MUTED' if profile.muted else '')]
                for i, band in enumerate(profile.bands, 1):
                    lines.append(f'{i}. {"ON" if band.enabled else "OFF"} · {band.kind} · {band.frequency:g} Hz · {band.gain:+g} dB · Q {band.q:g}')
                if not profile.bands:
                    lines.append('No filter bands')
                lines.append('Apply changes the current curve. Use Save as… to keep it as a preset.')
                review.set_text('\n'.join(lines))
                candidate = profile
                accept.set_sensitive(True)
            except ValueError as error:
                review.set_text(str(error))

        def choose_file(*_):
            chooser = Gtk.FileChooserNative.new('Open EQ file', dialog, Gtk.FileChooserAction.OPEN, 'Open', 'Cancel')
            dialog.file_chooser = chooser
            file_filter = Gtk.FileFilter()
            file_filter.set_name('EQ text or JSON')
            for pattern in ('*.txt', '*.json', '*.apo'):
                file_filter.add_pattern(pattern)
            chooser.add_filter(file_filter)
            all_files = Gtk.FileFilter(); all_files.set_name('All files'); all_files.add_pattern('*')
            chooser.add_filter(all_files)
            def selected(native, response):
                try:
                    if response == Gtk.ResponseType.ACCEPT:
                        file = native.get_file()
                        path = file.get_path() if file else None
                        if not path:
                            raise ValueError('Choose a local EQ file.')
                        with open(path, 'rb') as stream:
                            data = stream.read(MAX_IMPORT_BYTES+1)
                        if len(data) > MAX_IMPORT_BYTES:
                            raise ValueError('EQ files must be no larger than 1 MiB.')
                        source.get_buffer().set_text(data.decode('utf-8-sig'))
                        preview()
                except (OSError, ValueError) as error:
                    invalidate()
                    review.set_text(str(error))
                finally:
                    native.destroy()
                    dialog.file_chooser = None
            chooser.connect('response', selected)
            chooser.show()

        def apply_import(*_):
            if candidate is None:
                return
            self.remember()
            self.profile = copy.deepcopy(candidate)
            self.selected_band = None
            self.graph_limits = None
            self.preset.set_selected(Gtk.INVALID_LIST_POSITION)
            self.sync_preamp()
            self.render_bands()
            self.changed()
            dialog.close()

        actions = box()
        actions.append(button('Open file…', choose_file))
        actions.append(button('Review curve', preview))
        actions.append(button('Cancel', lambda *_: dialog.close()))
        accept = button('Apply imported curve', apply_import, 'suggested-action')
        accept.set_sensitive(False)
        actions.append(accept)
        content.append(actions)
        source.get_buffer().connect('changed', invalidate)
        name.connect('changed', invalidate)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        def dismiss(_, key, code, state):
            if key == Gdk.KEY_Escape:
                dialog.close()
                return True
            return False
        keys.connect('key-pressed', dismiss)
        dialog.add_controller(keys)
        def close_import(*_):
            if dialog.file_chooser is not None:
                dialog.file_chooser.destroy()
                dialog.file_chooser = None
            return False
        dialog.connect('close-request', close_import)
        dialog.set_child(content)
        dialog.present()
        source.grab_focus()

    def copy_eq(self, *_):
        try:
            text = self.profile.apo()
        except ValueError as e:
            self.status.set_text(str(e))
            return
        Gdk.Display.get_default().get_clipboard().set(text)
        self.status.set_text('Copied Equalizer APO text to the clipboard.')

    def toggle_bypass(self, *_):
        bypass = self.active
        self.run_work(lambda: self.engine.bypass(bypass), self.show_engine_state, "Bypassing…" if bypass else "Enabling…")

    def frequency_changed(self, w):
        self.tone.frequency = 20*1000**w.get_value()
        self.frequency_label.set_text(f'{self.tone.frequency:,.0f} Hz')
        self.graph.queue_draw()

    def toggle_tone(self, *_):
        if self.tone.process:
            self.stop_tone()
        else:
            try:
                self.tone.start(); self.play.set_label('Stop tone')
                self.status.set_text('Tone playing. Escape stops playback. Tone stops automatically after 60 seconds.')
                self.tone_deadline = GLib.get_monotonic_time()+60_000_000
            except OSError as e:
                self.status.set_text(str(e))

    def stop_tone(self):
        self.tone.stop(); self.play.set_label('Play tone')

    def key_pressed(self, controller, key, code, state):
        if key == Gdk.KEY_Escape:
            self.stop_tone(); return True
        return False

    def mark(self, name):
        self.marks[name] = self.tone.frequency
        self.mark_buttons[name].set_label(f'{name.capitalize()} {self.tone.frequency:.0f} Hz')

    def create_correction(self, *_):
        try:
            if len(self.profile.bands) >= 8:
                raise ValueError('Eight bands maximum')
            if not {'start', 'end'} <= self.marks.keys():
                raise ValueError('Mark start and end first')
            band = from_marks(self.marks['start'], self.marks['end'], dip=self.dip.get_active())
            self.remember(); self.profile.bands.append(band)
            self.marks.clear()
            for name, control in self.mark_buttons.items():
                control.set_label('Mark '+name)
            self.render_bands(); self.changed()
        except ValueError as e:
            self.status.set_text(str(e))

    def graph_geometry(self, width, height):
        left, top, right, bottom = 42, 28, max(43, width-20), max(29, height-30)
        if self.graph_limits is None:
            values = [self.profile.preamp, 0]
            for f in [20*1000**(i/160) for i in range(161)] + [b.frequency for b in self.profile.bands]:
                values.append(self.profile.preamp + self.profile.response(f, rate=self.sample_rate))
                values.extend(self.profile.preamp+b.response(f, rate=self.sample_rate) for b in self.profile.bands if b.enabled)
            low = min(-12, 6*math.floor(min(values)/6))
            high = max(12, 6*math.ceil(max(values)/6))
        else:
            low, high = self.graph_limits
        def x(f): return left + math.log(f/20)/math.log(1000)*(right-left)
        def y(db): return top+(high-db)/(high-low)*(bottom-top)
        return left, top, right, bottom, low, high, x, y

    def graph_hit(self, px, py):
        if self.profile.muted:
            return None
        *_, x, y = self.graph_geometry(self.graph.get_width(), self.graph.get_height())
        hits = [(math.hypot(px-x(b.frequency), py-y(self.profile.preamp+self.profile.response(b.frequency, rate=self.sample_rate))), i)
                for i, b in enumerate(self.profile.bands) if b.enabled]
        distance, index = min(hits, default=(math.inf, None))
        return index if distance <= 14 else None

    def graph_motion(self, controller, x, y):
        self.graph.set_cursor_from_name('move' if self.dragging is not None or self.graph_hit(x, y) is not None else 'default')

    def graph_drag_begin(self, gesture, x, y):
        index = self.graph_hit(x, y)
        self.selected_band = index
        self.graph.queue_draw()
        if index is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        left, top, right, bottom, low, high, *_ = self.graph_geometry(self.graph.get_width(), self.graph.get_height())
        self.graph_limits = (low, high)
        self.dragging = (index, self.profile.bands[index].gain, (high-low)/(bottom-top), copy.deepcopy(self.profile), right-left)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.graph.set_cursor_from_name('move')
        self.graph.queue_draw()

    def graph_drag_update(self, gesture, dx, dy):
        if self.dragging is None:
            return
        index, initial_gain, db_per_pixel, before, span = self.dragging
        band = self.profile.bands[index]
        # Shelf response at its center is half its gain in dB.
        factor = 1 if band.kind == 'Bell' else 2
        band.gain = round(max(-12, min(12, initial_gain-dy*db_per_pixel*factor)), 1)
        band.frequency = round(max(20, min(20000, before.bands[index].frequency * 1000**max(-1, min(1, dx/span)))))
        self.syncing_gain = True
        try:
            self.gain_spins[index].set_value(band.gain)
            self.band_spins[index]["frequency"].set_value(band.frequency)
        finally:
            self.syncing_gain = False
        self.graph.queue_draw()
        self.schedule_live()
        self.status.set_text(f'Band {index+1}: {band.frequency:,.0f} Hz · {band.gain:+.1f} dB · Q {band.q:.2f}')

    def graph_drag_end(self, gesture, dx, dy):
        if self.dragging is None:
            return
        _, _, _, before, _ = self.dragging
        self.dragging = None
        self.graph_limits = None
        self.graph.set_cursor_from_name('default')
        if before != self.profile:
            self.history.append(before)
            self.history = self.history[-40:]
            self.changed()
        self.graph.queue_draw()

    def graph_scroll(self, controller, dx, dy):
        index = self.selected_band
        if index is None or index >= len(self.profile.bands) or not self.profile.bands[index].enabled:
            return False
        band = self.profile.bands[index]
        value = round(max(.3, min(10, band.q * 1.12**max(-20, min(20, -dy)))), 2)
        if value != band.q:
            if self.dragging is None:
                self.remember()
            band.q = value
            self.syncing_gain = True
            self.band_spins[index]['q'].set_value(value)
            self.syncing_gain = False
            self.changed()
            self.status.set_text(f'Band {index+1}: Q {value:.2f}')
        return True

    def layout_changed(self):
        if not hasattr(self, 'panel_order'):
            return
        self.ui = {'order': self.panel_order, 'expanded': {k:p.toggle.get_active() for k,p in self.panels.items()},
                   'overlay': self.overlay.get_active()}
        try:
            atomic_json(self.ui_path, self.ui)
        except OSError as error:
            self.status.set_text(f'Could not save layout: {error}')
        self.graph.queue_draw()

    def reorder_panels(self):
        previous = None
        for key in self.panel_order:
            self.panel_box.reorder_child_after(self.panels[key], previous)
            previous = self.panels[key]
        self.layout_changed()

    def move_panel(self, key, offset):
        index = self.panel_order.index(key)
        target = max(0, min(len(self.panel_order)-1, index+offset))
        self.panel_order.pop(index)
        self.panel_order.insert(target, key)
        self.reorder_panels()

    def drop_panel(self, key, target, after):
        if key not in self.panels or key == target:
            return False
        self.panel_order.remove(key)
        self.panel_order.insert(self.panel_order.index(target)+int(after), key)
        self.reorder_panels()
        return True

    def spectrum_tick(self):
        if self.closed:
            return False
        overlay = self.overlay.get_active() and self.panels['eq'].toggle.get_active()
        visible = self.panels['rta'].toggle.get_active()
        output = self.monitor_output
        if not output and self.device.get_selected() < len(self.devices):
            output = self.devices[self.device.get_selected()][0]
        self.analyzer.set_source(output+'.monitor' if output and (overlay or visible) else None)
        if visible:
            self.rta.queue_draw()
            self.rta_status.set_text(self.analyzer.message)
        if overlay:
            self.graph.queue_draw()
        return True

    def draw_rta(self, widget, cr, width, height):
        left, top, right, bottom = 35, 12, max(36,width-15), max(13,height-25)
        cr.select_font_face('monospace'); cr.set_font_size(10)
        cr.set_source_rgba(*(int(self.muted[i:i+2],16)/255 for i in (1,3,5)), .6)
        for db in (0,-30,-60,-90):
            y = top-db/90*(bottom-top)
            cr.move_to(0,y+4); cr.show_text(str(db))
            cr.move_to(left,y); cr.line_to(right,y); cr.set_line_width(.4); cr.stroke()
        for f in (20,100,1000,10000,20000):
            x = left+math.log(f/20)/math.log(1000)*(right-left)
            cr.move_to(x-10,height-5); cr.show_text(f'{f//1000}k' if f>=1000 else str(f))
        draw_bars(cr, self.analyzer.levels, self.accent, left, top, right, bottom, .8)

    def draw_graph(self, widget, cr, width, height):
        def color(hex_color, alpha=1):
            value = hex_color.lstrip('#')
            cr.set_source_rgba(*(int(value[i:i+2],16)/255 for i in (0,2,4)), alpha)
        left, top, right, bottom, low, high, x, y = self.graph_geometry(width, height)
        cr.select_font_face('monospace'); cr.set_font_size(10)
        for db in range(low, high+1, 6):
            color(self.muted,.35 if db==0 else .15); cr.set_line_width(1)
            cr.move_to(left,y(db)); cr.line_to(right,y(db)); cr.stroke()
            color(self.muted); cr.move_to(2,y(db)+4); cr.show_text(f'{db:+d}')
        for f in (20,50,100,200,500,1000,2000,5000,10000,20000):
            color(self.muted,.15); cr.move_to(x(f),top); cr.line_to(x(f),bottom); cr.stroke()
            color(self.muted); cr.move_to(x(f)-10,height-8); cr.show_text(f'{f//1000}k' if f>=1000 else str(f))
        if self.overlay.get_active():
            draw_bars(cr, self.analyzer.levels, self.accent, left, top, right, bottom, .16)
        if self.profile.muted:
            color(self.muted); cr.move_to(left, 12); cr.show_text('Preamp muted (−∞ dB). Raise the fader to restore the response.')
            return
        rate_label = f'{self.sample_rate/1000:g} kHz engine' if self.sample_rate_known else '48 kHz preview (engine rate unavailable)'
        color(self.muted); cr.move_to(left,12); cr.show_text(f'EQ + preamp {self.profile.preamp:+.1f} dB · {rate_label} · drag: frequency / gain · scroll: Q')
        cr.save(); cr.rectangle(left,top,right-left,bottom-top); cr.clip()
        for band in self.profile.bands:
            color(self.muted,.45); cr.set_line_width(1)
            for i in range(321):
                f=20*1000**(i/320)
                (cr.move_to if i==0 else cr.line_to)(x(f),y(self.profile.preamp+band.response(f, rate=self.sample_rate)))
            cr.stroke()
        color(self.ink); cr.set_line_width(2)
        for i in range(481):
            f=20*1000**(i/480)
            (cr.move_to if i==0 else cr.line_to)(x(f),y(self.profile.preamp+self.profile.response(f, rate=self.sample_rate)))
        cr.stroke()
        color(self.accent,.6); cr.set_dash([3,4]); cr.set_line_width(1)
        cr.move_to(x(self.tone.frequency),top); cr.line_to(x(self.tone.frequency),bottom); cr.stroke(); cr.set_dash([])
        cr.restore()
        for index, band in enumerate(self.profile.bands):
            if band.enabled:
                px, py = x(band.frequency), y(self.profile.preamp+self.profile.response(band.frequency, rate=self.sample_rate))
                color(self.accent if self.selected_band == index else self.ink)
                cr.set_line_width(2); cr.new_sub_path(); cr.arc(px,py,7,0,2*math.pi); cr.stroke()
                cr.move_to(px+10,py-8); cr.show_text(str(index+1))

    def tick(self):
        if self.closed:
            return False
        self.load_theme()
        self.preamp.track.queue_draw()
        if not self.busy and not self.live_timer and self.connected_once:
            self.run_work(self.engine.status, self.show_engine_state)
        if self.tone.process and (self.tone.error or GLib.get_monotonic_time()>self.tone_deadline):
            message = self.tone.error or 'Tone stopped after 60 seconds.'
            self.stop_tone(); self.status.set_text(message)
        return True

    def close(self, *_):
        self.closed = True
        self.analyzer.stop()
        self.stop_tone()
        return False

class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='com.eqxear.App', flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        window = self.get_active_window() or Window(self)
        window.present()

def main():
    return App().run(None)
