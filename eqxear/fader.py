# SPDX-License-Identifier: Apache-2.0
"""Console-style dB taper and a native GTK level fader."""
import math
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

MIN_DB, MAX_DB = -96.0, 18.0
# Travel from bottom to top. dB interpolation gives logarithmic amplitude;
# wider spacing near unity gives finer control there.
TAPER = ((.02, -96), (.12, -60), (.28, -36), (.50, -12), (.75, 0), (1., 18))


def position_to_db(position):
    if position <= .005:
        return None
    position = max(TAPER[0][0], min(1., position))
    for (p0, d0), (p1, d1) in zip(TAPER, TAPER[1:]):
        if position <= p1:
            return round(d0+(position-p0)/(p1-p0)*(d1-d0), 1)
    return MAX_DB


def db_to_position(db):
    if db is None:
        return 0.
    db = max(MIN_DB, min(MAX_DB, db))
    for (p0, d0), (p1, d1) in zip(TAPER, TAPER[1:]):
        if db <= d1:
            return p0+(db-d0)/(d1-d0)*(p1-p0)
    return 1.


class LevelFader(Gtk.Box):
    def __init__(self, on_change, on_begin, on_end, colors):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.on_change, self.on_begin, self.on_end = on_change, on_begin, on_end
        self.colors = colors
        self.value = 0.
        self.drag_origin = None
        self.append(Gtk.Label(label='PREAMP'))
        self.track = Gtk.DrawingArea(content_width=125, content_height=200, vexpand=True)
        self.track.set_draw_func(self.draw)
        self.track.set_cursor_from_name('ns-resize')
        self.track.set_tooltip_text('Drag to adjust preamp gain. 0 dB is unity; the bottom is mute. Changes are heard live while system EQ is running.')
        self.append(self.track)
        drag = Gtk.GestureDrag.new(); drag.set_button(1)
        drag.connect('drag-begin', self.begin)
        drag.connect('drag-update', self.update)
        drag.connect('drag-end', self.end)
        drag.connect('cancel', lambda *_: self.end(None, 0, 0))
        self.track.add_controller(drag)
        row = Gtk.Box(spacing=3)
        minus = Gtk.Button(label='−'); minus.connect('clicked', lambda *_: self.step(-.1))
        row.append(minus)
        self.entry = Gtk.Entry(width_chars=6, max_width_chars=7, xalign=.5)
        self.entry.set_tooltip_text('Preamp in dB, from −96 to +18. Enter −inf to mute.')
        self.entry.connect('activate', self.commit_text)
        focus = Gtk.EventControllerFocus.new(); focus.connect('leave', self.commit_text)
        self.entry.add_controller(focus)
        row.append(self.entry)
        plus = Gtk.Button(label='+'); plus.connect('clicked', lambda *_: self.step(.1)); row.append(plus)
        self.append(row)
        self.caption = Gtk.Label(label='dB'); self.caption.add_css_class('muted'); self.append(self.caption)
        self.set_value(0.)

    def set_value(self, db):
        self.value = db
        self.entry.set_text('−∞' if db is None else f'{db:+.1f}')
        self.entry.remove_css_class('error')
        self.caption.set_text('dB')
        self.track.queue_draw()

    def get_value(self):
        return self.value

    def commit_text(self, *_):
        text = self.entry.get_text().strip().lower().replace('−', '-')
        try:
            value = None if text in ('-inf', '-infinity', '-∞', '∞', 'mute') else float(text)
            if value is not None and (not math.isfinite(value) or not MIN_DB <= value <= MAX_DB):
                raise ValueError()
        except ValueError:
            self.entry.add_css_class('error')
            self.caption.set_text('Use −96…+18 or −inf')
            return
        self.caption.set_text('dB')
        self.on_change(None if value is None else round(value, 1))
        self.set_value(self.value)

    def step(self, delta):
        if self.value is None:
            value = MIN_DB if delta > 0 else None
        else:
            value = None if self.value+delta < MIN_DB-.001 else round(min(MAX_DB, self.value+delta), 1)
        self.on_change(value)

    def begin(self, gesture, x, y):
        self.on_begin()
        self.drag_origin = db_to_position(self.value)
        self.track_height = max(1, self.track.get_height()-32)
        dot_y = 16+(1-self.drag_origin)*self.track_height
        if abs(y-dot_y) > 14:
            self.drag_origin = max(0, min(1, 1-(y-16)/self.track_height))
            self.on_change(position_to_db(self.drag_origin))
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def update(self, gesture, dx, dy):
        if self.drag_origin is not None:
            self.on_change(position_to_db(self.drag_origin-dy/self.track_height))

    def end(self, gesture, dx, dy):
        if self.drag_origin is not None:
            self.drag_origin = None
            self.on_end()

    def draw(self, widget, cr, width, height):
        ink, muted, accent = self.colors()
        def color(value):
            cr.set_source_rgb(*(int(value[i:i+2],16)/255 for i in (1,3,5)))
        x, top, travel = width*.4, 16, max(1,height-32)
        color(muted); cr.set_line_width(1); cr.move_to(x,top); cr.line_to(x,top+travel); cr.stroke()
        cr.select_font_face('monospace'); cr.set_font_size(11)
        for db in (18, 6, 0, -6, -12, -24, -48, -96, None):
            py = top+(1-db_to_position(db))*travel
            # Bottom label is infinity; -96 is too close to label separately.
            if db == -96: continue
            color(ink if db == 0 else muted)
            cr.set_line_width(2 if db == 0 else 1)
            cr.move_to(x-6,py); cr.line_to(x+6,py); cr.stroke()
            cr.move_to(x+13,py+4); cr.show_text('−∞' if db is None else ('0 U' if db == 0 else f'{db:+g}'))
        py = top+(1-db_to_position(self.value))*travel
        color(accent); cr.new_sub_path(); cr.arc(x,py,6,0,2*math.pi); cr.fill()
