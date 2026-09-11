# SPDX-License-Identifier: Apache-2.0
"""Exercise GTK callbacks against temporary data; never plays or applies audio."""
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
import cairo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['GTK_A11Y'] = 'none'  # The isolated smoke test does not need the desktop accessibility bus.
temporary_environment = tempfile.TemporaryDirectory()
for variable, directory in (('XDG_DATA_HOME', 'data'), ('XDG_CACHE_HOME', 'cache'), ('XDG_RUNTIME_DIR', 'runtime')):
    location = Path(temporary_environment.name)/directory
    location.mkdir(mode=0o700)
    os.environ[variable] = str(location)
from eqxear.app import App, Window, GLib, Gio
from eqxear.model import Band, Profile

with tempfile.TemporaryDirectory() as temp:
    os.environ['XDG_DATA_HOME']=temp
    app=App(); app.set_application_id('com.eqxear.Smoke'); app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
    app.register()
    with patch.object(Window, 'refresh_outputs'):
        window=Window(app)
    for _ in range(20):
        while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
        time.sleep(.01)
    window.add_band()
    window.edit_band(window.profile.bands[0],'gain',4)
    window.normalize_peak()
    assert window.profile.preamp <= -4
    window.marks={'start':500,'center':1000,'end':1500}
    window.create_correction()
    assert len(window.profile.bands)==2
    window.remove_band(0)
    window.undo()
    assert len(window.profile.bands)==2
    assert window.draft_path.exists()
    window.profile = Profile('Drag test', [Band(29, 11.5, .3)])
    window.render_bands()
    window.normalize_peak()
    assert window.profile.preamp == -11.5
    class Gesture:
        def set_state(self, state): self.state = state
    gesture = Gesture()
    with patch.object(window.graph, 'get_width', return_value=1000), patch.object(window.graph, 'get_height', return_value=240):
        _, top, _, bottom, low, high, x, y = window.graph_geometry(1000, 240)
        px, py = x(29), y(0)
        assert window.graph_hit(px, py) == 0
        assert window.graph_hit(900, 200) is None
        history_count = len(window.history)
        window.graph_drag_begin(gesture, px, py)
        window.graph_drag_update(gesture, 100, (bottom-top)/(high-low)*3)
        assert window.profile.bands[0].gain == 8.5
        assert window.gain_spins[0].get_value() == 8.5
        assert window.profile.bands[0].frequency == round(29*1000**(100/938))
        assert window.band_spins[0]["frequency"].get_value() == window.profile.bands[0].frequency
        assert window.profile.preamp == -11.5
        window.graph_drag_update(gesture, 0, -10000)
        assert window.profile.bands[0].gain == 12
        window.graph_drag_end(gesture, 0, 0)
        assert len(window.history) == history_count+1
        window.undo()
        assert window.profile.bands[0].gain == 11.5
        # Shelf handles move by half the filter gain at the center frequency.
        window.profile = Profile('Shelf', [Band(1000, 2, .7, 'Lo-shelf')])
        window.render_bands()
        _, top, _, bottom, low, high, x, y = window.graph_geometry(1000, 240)
        window.graph_drag_begin(gesture, x(1000), y(1))
        window.graph_drag_update(gesture, 0, -(bottom-top)/(high-low))
        assert window.profile.bands[0].gain == 4
        window.graph_drag_end(gesture, 0, 0)
        window.profile.bands[0].enabled = False
        assert window.graph_hit(x(1000), y(0)) is None
    window.profile.bands[0].enabled = True
    window.selected_band = 0
    old_q = window.profile.bands[0].q
    assert window.graph_scroll(None, 0, -1)
    assert window.profile.bands[0].q > old_q
    assert window.band_spins[0]['q'].get_value() == window.profile.bands[0].q
    window.undo()
    assert window.profile.bands[0].q == old_q
    window.drop_panel('rta', 'controls', False)
    assert window.panel_order[0] == 'rta'
    window.panels['eq'].toggle.set_active(False)
    assert not window.panels['eq'].content.get_visible()
    import json
    saved = json.loads(window.ui_path.read_text())
    assert saved['order'][0] == 'rta' and not saved['expanded']['eq']
    window.panels['eq'].toggle.set_active(True)
    window.present()
    for _ in range(15):
        while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
        time.sleep(.01)
    from gi.repository import Gtk
    b = window.apply_button
    for x,y in ((8,8),(b.get_width()-8,8),(8,b.get_height()-8),(b.get_width()/2,b.get_height()/2)):
        picked = b.pick(x,y,Gtk.PickFlags.DEFAULT)
        assert picked is not None, (x,y,b.get_width(),b.get_height())
        while picked is not b and picked is not None:
            picked = picked.get_parent()
        assert picked is b
    window.busy = True
    with patch.object(window.engine, 'start') as start:
        window.run_work(start, lambda _: None, 'Starting…')
        assert window.apply_button.get_label() == 'Starting…'
        assert not window.apply_button.get_sensitive()
        assert not start.called
    window.pending_actions.clear()
    window.busy = False
    window.apply_button.set_sensitive(True)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1000, 240)
    window.draw_graph(window.graph, cairo.Context(surface), 1000, 240)
    window.draw_rta(window.rta, cairo.Context(surface), 1000, 240)
    # Normalization, mouse, numeric entry, and incremental controls stay in sync.
    window.profile = Profile('Fader', [Band(29,11.5,.3)])
    window.normalize_peak()
    assert window.preamp.get_value() == -11.5
    window.preamp.entry.set_text('-6.0'); window.preamp.commit_text()
    assert window.profile.preamp == -6
    window.preamp.step(.1); assert window.profile.preamp == -5.9
    count = len(window.history)
    with patch.object(window.preamp.track, 'get_height', return_value=240):
        from eqxear.fader import db_to_position
        py = 16+(1-db_to_position(-5.9))*208
        window.preamp.begin(gesture, 50, py)
        window.preamp.update(gesture, 0, -40)
        assert window.profile.preamp > -5.9
        window.preamp.end(gesture, 0, -40)
    assert len(window.history) == count+1
    window.undo(); assert window.preamp.get_value() == -5.9
    window.preamp.entry.set_text('-inf'); window.preamp.commit_text()
    assert window.profile.muted and window.preamp.get_value() is None
    window.draw_graph(window.graph, cairo.Context(surface), 1000, 240)
    window.preamp.step(.1)
    assert not window.profile.muted and window.profile.preamp == -96
    window.preamp.entry.set_text('nan'); window.preamp.commit_text()
    assert window.profile.preamp == -96
    window.normalize_peak()
    assert not window.profile.muted and window.preamp.get_value() == -11.5
    window.preamp.draw(window.preamp.track, cairo.Context(surface), 160, 240)
    with patch.object(window.engine, 'apply', return_value={'running':True, 'bypassed':False}) as apply:
        window.engine.owned = True
        window.preamp_changed(-6)
        for _ in range(30):
            while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
            time.sleep(.01)
        assert apply.called
        assert apply.call_args[0][0].preamp == -6
        window.engine.owned = False
    # Deletion persists, preserves the live curve, and handles an empty library.
    import copy
    from gi.repository import Gtk
    window.profiles = [Profile('Speakers', [Band(80,-3)]), Profile('Headphones', [Band(1000,2)])]
    window.library.save(window.profiles)
    window.profile = copy.deepcopy(window.profiles[0])
    current = copy.deepcopy(window.profile)
    window.preset.set_model(Gtk.StringList.new([p.name for p in window.profiles]))
    window.preset.set_selected(0)
    window.delete_preset_dialog()
    dialog = next(w for w in Gtk.Window.list_toplevels() if w.get_title() == 'Delete preset')
    dialog.close()
    assert len(window.library.load()) == 2
    with patch.object(window.library, 'save', side_effect=OSError('Read-only filesystem')):
        assert not window.delete_preset(0)
    assert len(window.profiles) == 2 and len(window.library.load()) == 2
    with patch.object(window.engine, 'apply') as apply:
        assert window.delete_preset(0)
        assert window.profile == current
        assert window.library.load() == window.profiles
        assert window.profiles[0].name == 'Headphones'
        assert window.delete_preset(0)
        assert window.library.load() == []
        assert window.profile == current
        assert not window.delete_button.get_sensitive()
        assert not window.recall_button.get_sensitive()
        assert not apply.called
    assert not window.delete_preset(0)
    window.save_dialog()
    dialog = next(w for w in Gtk.Window.list_toplevels() if w.get_title() == 'Save preset')
    entry = dialog.get_child().get_first_child().get_next_sibling()
    entry.set_text('Saved with Enter')
    entry.emit('activate')
    assert len(window.library.load()) == 1
    assert window.delete_button.get_sensitive() and window.recall_button.get_sensitive()
    assert window.library.load()[0].name == 'Saved with Enter'
    saved_library = window.library.path.read_bytes()
    saved_draft = window.draft_path.read_bytes()
    current = copy.deepcopy(window.profile)
    window.save_dialog()
    dialog = next(w for w in Gtk.Window.list_toplevels() if w.get_title() == 'Save preset')
    entry = dialog.get_child().get_first_child().get_next_sibling()
    entry.set_text('')
    entry.emit('activate')
    assert dialog.get_visible()  # Invalid names leave the dialog open to correct or cancel.
    entry.set_text('Must not save')
    from gi.repository import Gdk
    keys = next(c for c in dialog.observe_controllers() if isinstance(c, Gtk.EventControllerKey))
    assert keys.emit('key-pressed', Gdk.KEY_Escape, 0, Gdk.ModifierType(0))
    assert not dialog.get_visible()
    assert window.library.path.read_bytes() == saved_library
    assert window.draft_path.read_bytes() == saved_draft
    assert window.profile == current
    window.save_dialog()
    dialog = next(w for w in Gtk.Window.list_toplevels() if w.get_title() == 'Save preset')
    dialog.get_child().get_last_child().get_first_child().emit('clicked')
    assert not dialog.get_visible()
    assert window.library.path.read_bytes() == saved_library
    # Library publication succeeds even if updating the recovery draft fails.
    window.save_dialog()
    dialog = next(w for w in Gtk.Window.list_toplevels() if w.get_title() == 'Save preset')
    entry = dialog.get_child().get_first_child().get_next_sibling()
    entry.set_text('Saved despite draft failure')
    with patch('eqxear.app.atomic_json', side_effect=OSError('Draft disk failure')):
        entry.emit('activate')
    assert not dialog.get_visible()
    assert window.library.load()[-1].name == 'Saved despite draft failure'
    assert window.preset.get_selected_item().get_string() == 'Saved despite draft failure'
    assert 'Saved despite draft failure' in window.status.get_text()
    assert 'Draft could not be saved' in window.status.get_text()

    def settle():
        deadline = time.monotonic()+3
        while window.busy or window.pending_actions:
            assert time.monotonic() < deadline, 'UI work queue stalled'
            GLib.MainContext.default().iteration(False)
            time.sleep(.005)

    # A stopped connected engine must not replace a newer local draft.
    window.profile = Profile('Newer draft', [Band(1000, -8)])
    window.connected_once = False
    old_state = {'running': False, 'connected': True, 'output': 'sink-a',
                 'profile': Profile('Older engine', [Band(1000, 3)]).to_dict()}
    with patch('eqxear.app.outputs', return_value=[('sink-a', 'Output A')]), patch.object(window.engine, 'status', return_value=old_state):
        window.refresh_outputs()
        settle()
    assert window.profile.name == 'Newer draft' and window.profile.bands[0].gain == -8

    # Explicit actions survive a busy poll; output changes coalesce to the latest.
    import threading
    gate = threading.Event()
    calls = []
    window.engine.owned = True
    window.devices = [('sink-a', 'Output A'), ('sink-b', 'Output B')]
    window.device.set_model(Gtk.StringList.new(['Output A', 'Output B']))
    state = {'running': True, 'connected': True, 'output': 'sink-b'}
    with patch.object(window.engine, 'select_output', side_effect=lambda name: calls.append(name) or state), patch('eqxear.app.outputs', return_value=window.devices), patch.object(window.engine, 'status', return_value=state):
        window.run_work(lambda: gate.wait(2), lambda _: window.show_engine_state([]))
        window.device.set_selected(0); window.set_output()
        window.device.set_selected(1); window.set_output()
        window.refresh_outputs()
        gate.set()
        settle()
    assert calls == ['sink-b']
    assert not window.pending_actions and window.apply_button.get_sensitive()
    window.run_work(lambda: [], window.show_engine_state)
    settle()
    assert 'Invalid engine response' in window.status.get_text()
    window.engine.owned = False
    window.connected_once = False

    # Explicit background quit supersedes pending startup and preserves the
    # editor, while even an initial refresh cannot automatically restart it.
    window.engine.owned = True
    window.connected_once = False
    before_quit = copy.deepcopy(window.profile)
    gate = threading.Event()
    with patch.object(window.engine, 'disconnect', return_value={'running': False}) as disconnect, patch.object(window.engine, 'start') as start, patch.object(window.engine, 'status', return_value={'running': False}), patch('eqxear.app.outputs', return_value=[('sink-a', 'Output A')]):
        window.run_work(lambda: gate.wait(2), lambda _: None)
        window.start_engine()
        window.quit_service_button.emit('clicked')
        assert window.apply_button.get_label() == 'Quitting…'
        assert not window.quit_service_button.get_sensitive()
        gate.set()
        settle()
        disconnect.assert_called_once()
        start.assert_not_called()
        assert window.profile == before_quit
        assert not window.engine.owned
        assert window.apply_button.get_label() == 'Start system EQ'
        assert 'Background service quit' in window.status.get_text()
        assert window.quit_service_button.get_sensitive()
        window.refresh_outputs()
        settle()
        start.assert_not_called()
    # A failed quit remains visible and retryable; it cannot silently claim
    # success or leave the action disabled.
    with patch.object(window.engine, 'disconnect', side_effect=RuntimeError('Disconnect failed')):
        window.quit_service_button.emit('clicked')
        settle()
        assert 'Disconnect failed' in window.status.get_text()
        assert window.quit_service_button.get_sensitive()
    window.connected_once = False

    # Malformed draft bytes remain recoverable even after an edit.
    window.draft_path.write_text('[null]')
    original = window.draft_path.read_bytes()
    with patch.object(Window, 'refresh_outputs'):
        recovery = Window(app)
    assert recovery.draft_error
    recovery.add_band()
    assert recovery.draft_path.read_bytes() == original
    assert 'Original file left intact' in recovery.status.get_text()
    recovery.draft_path.unlink()
    recovery.changed()
    assert Profile.from_dict(json.loads(recovery.draft_path.read_text())) == recovery.profile
    recovery.close()
    assert window.tone.process is None
    with patch.object(window.engine, 'disconnect') as disconnect:
        window.close()
        disconnect.assert_not_called()
    print('PASS: GTK controls, normalization, graph dragging, gain limits, shelf dragging, single-step undo, draft persistence, graph rendering, silent close')

temporary_environment.cleanup()
