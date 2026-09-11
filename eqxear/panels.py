# SPDX-License-Identifier: Apache-2.0
"""Collapsible panels with header drag-and-drop and keyboard reordering."""
from gi.repository import Gtk, Gdk


class Panel(Gtk.Box):
    def __init__(self, key, title, content, expanded, changed, move):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.key = key
        header = Gtk.Box(spacing=8)
        self.toggle = Gtk.ToggleButton(hexpand=True, halign=Gtk.Align.FILL)
        self.caption = Gtk.Label(xalign=0)
        self.toggle.set_child(self.caption)
        self.content = content
        self.title = title
        self.toggle.set_active(expanded)
        self.toggle.connect('toggled', lambda *_: (self.update(), changed()))
        header.append(self.toggle)
        handle = Gtk.Label(label='⠿', focusable=True)
        handle.set_size_request(32, 32)
        handle.set_cursor_from_name('grab')
        handle.set_tooltip_text('Drag to reorder. With this handle focused, use Alt+Up or Alt+Down.')
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect('prepare', lambda *_: Gdk.ContentProvider.new_for_value(key))
        handle.add_controller(source)
        keys = Gtk.EventControllerKey()
        def pressed(_, keyval, code, state):
            if state & Gdk.ModifierType.ALT_MASK and keyval in (Gdk.KEY_Up,Gdk.KEY_Down):
                move(key, -1 if keyval == Gdk.KEY_Up else 1)
                return True
            return False
        keys.connect('key-pressed', pressed)
        handle.add_controller(keys)
        header.append(handle)
        self.append(header)
        self.append(content)
        self.update()

    def update(self):
        self.content.set_visible(self.toggle.get_active())
        self.caption.set_text(('▾  ' if self.toggle.get_active() else '▸  ')+self.title)
