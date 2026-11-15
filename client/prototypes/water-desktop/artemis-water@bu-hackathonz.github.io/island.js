// The Artemis top-edge island: a glowing pill hanging under the top bar, joined to a light line
// along the bottom edge of the bar, with the ring, the "artemis." name, a waveform and a chevron.
//   - the chevron (or the waveform) opens a panel: type a request for the Artemis core, or Stop
//   - while a request is in progress the pill says so, the waveform moves and a pulse of light runs
//     along the edge; when it ends the ring shows a check for a few seconds
//   - setView() takes islandState.view() (contracts.md §8). The look is the approved one: the eight
//     states map onto its idle, working and done looks; any new look per state is Jun's call
// The droplet that flies through a command is drawn by the extension (ringEffect); it leaves from
// and returns to ringCenter(). The island reserves its strip at the top of the screen, so
// maximized windows sit below it instead of under it.

import GLib from 'gi://GLib';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Cairo from 'gi://cairo';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const PURPLE = [0.66, 0.33, 0.97];
const GAP = -4;          // logical px between the top bar and the top of the pill
const SHOULDER = 46;     // logical px the edge line takes to curve into the pill
const DONE_MS = 7000;    // how long "Done" stays

// The core's states shown with the approved working look (the others look idle, or done).
const WORKING = new Set(['listening', 'thinking', 'editing', 'running', 'speaking']);

export class Island {
    /**
     * opts: {onAsk(text) -> Promise<{ok, message?}>, onStop(), onSettings(), onMoved(), font}
     */
    constructor(opts) {
        this._opts = opts;
        this._state = 'idle';
        this._phase = 0;
        this._scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;

        // The light along the edge (non-reactive: clicks pass through to the top bar and windows).
        this._edge = new St.DrawingArea({reactive: false});
        this._edge.connect('repaint', (area) => this._paintEdge(area));
        // No strut: reserving the strip pushed maximized windows below the pill (a gap under the bar).
        Main.layoutManager.addChrome(this._edge, {affectsStruts: false, trackFullscreen: true});

        // The pill.
        this._pill = new St.BoxLayout({style_class: 'artemis-island', reactive: true, y_align: Clutter.ActorAlign.CENTER});
        this._ring = new St.Widget({style_class: 'artemis-island-ring', y_align: Clutter.ActorAlign.CENTER});
        this._name = new St.Label({style_class: 'artemis-island-name', text: 'artemis.', y_align: Clutter.ActorAlign.CENTER});
        this._wave = new St.DrawingArea({style_class: 'artemis-island-wave'});
        this._wave.connect('repaint', (area) => this._paintWave(area));
        this._status = new St.Label({style_class: 'artemis-island-status', text: '', y_align: Clutter.ActorAlign.CENTER, visible: false});
        this._waveButton = new St.Button({style_class: 'artemis-island-wave-button', can_focus: true, accessible_name: 'Ask Artemis', child: this._wave});
        this._waveButton.connect('clicked', () => this.toggle());
        const chevronIcon = new St.Icon({icon_name: 'pan-down-symbolic', style_class: 'artemis-island-chevron-icon'});
        this._chevron = new St.Button({style_class: 'artemis-island-chevron', can_focus: true, accessible_name: 'Open Artemis', child: chevronIcon});
        this._chevron.connect('clicked', () => this.toggle());
        const sep = () => new St.Widget({style_class: 'artemis-island-sep', y_align: Clutter.ActorAlign.CENTER});
        for (const child of [this._ring, sep(), this._waveButton, this._chevron]) this._pill.add_child(child);
        Main.layoutManager.addChrome(this._pill, {trackFullscreen: true});
        // The summary tab: what the last command did, beside the pill.
        this._tab = new St.BoxLayout({style_class: 'artemis-island-tab', vertical: true, visible: false, reactive: true});
        this._tabTitle = new St.Label({style_class: 'artemis-island-tab-title'});
        this._tabText = new St.Label({style_class: 'artemis-island-tab-text'});
        this._tabText.clutter_text.line_wrap = true;
        this._tabStop = new St.Button({style_class: 'artemis-island-tab-stop', label: 'Stop', can_focus: true, x_align: Clutter.ActorAlign.START});
        this._tabStop.connect('clicked', () => this._opts.onStop());
        for (const c of [this._tabTitle, this._tabText, this._tabStop]) this._tab.add_child(c);
        // The board is never put on screen (removed by request); kept only so old calls stay harmless.
        try { this._tab.add_effect(new Shell.BlurEffect({mode: Shell.BlurMode.BACKGROUND, brightness: 0.75})); } catch { /* no blur */ }
        this._tab.connect('notify::width', () => this._layout());
        // Frosted glass: blur what's behind the pill (like Windows acrylic), tinted by the CSS.
        try {
            const blur = new Shell.BlurEffect({mode: Shell.BlurMode.BACKGROUND, brightness: 0.75});
            if ('radius' in blur) blur.radius = 24; else blur.sigma = 12;
            if ('corner_radius' in blur) blur.corner_radius = 20;
            this._pill.add_effect(blur);
        } catch (e) { console.warn(`Artemis: no blur (${e.message})`); }
        this._pill.connect('destroy', () => { this._dead = true; }); // GNOME can destroy chrome first at shutdown

        // The panel: a popup menu under the pill (keyboard focus, Escape and modality as GNOME's own).
        this._menu = new PopupMenu.PopupMenu(this._chevron, 0.5, St.Side.TOP);
        this._menu.actor.add_style_class_name('artemis-island-menu');
        Main.uiGroup.add_child(this._menu.actor);
        this._menu.actor.hide();
        this._menuManager = new PopupMenu.PopupMenuManager(this._pill);
        this._menuManager.addMenu(this._menu);
        this._fillMenu();
        this._menu.connect('open-state-changed', (_m, open) => {
            chevronIcon.icon_name = open ? 'pan-up-symbolic' : 'pan-down-symbolic';
            this._chevron.accessible_name = open ? 'Close Artemis' : 'Open Artemis';
            if (open) {
                this._hint.text = '';
                GLib.idle_add(GLib.PRIORITY_DEFAULT, () => { this._entry?.grab_key_focus(); return GLib.SOURCE_REMOVE; });
            }
        });

        this._pill.connect('notify::width', () => this._layout());
        this._pill.connect('notify::height', () => this._layout());
        this._monitorsId = Main.layoutManager.connect('monitors-changed', () => this._layout());
        this._panelId = Main.layoutManager.panelBox.connect('notify::height', () => this._layout());
        if (opts.font) for (const a of [this._tab, this._menu.actor]) a.style = `font-family: "${opts.font}";`;
        this._layout();
    }

    destroy() {
        if (this._timer) GLib.source_remove(this._timer);
        if (this._doneTimer) GLib.source_remove(this._doneTimer);
        this._timer = this._doneTimer = 0;
        if (this._monitorsId) Main.layoutManager.disconnect(this._monitorsId);
        if (this._panelId) Main.layoutManager.panelBox.disconnect(this._panelId);
        this._menu?.destroy();
        this._tab?.destroy();
        for (const actor of [this._pill, this._edge]) {
            if (!actor) continue;
            Main.layoutManager.removeChrome(actor);
            actor.destroy();
        }
        this._menu = this._pill = this._edge = this._tab = null;
    }

    /** Where the ring sits (stage px): the droplet leaves from and returns to here. */
    ringCenter() {
        const [x, y] = this._dead ? [0, 0] : this._ring.get_transformed_position();
        const [w, h] = this._dead ? [0, 0] : this._ring.get_transformed_size();
        if (!w) {
            const m = Main.layoutManager.primaryMonitor;
            return {x: m.x + m.width / 2, y: m.y + Main.layoutManager.panelBox.height + 40 * this._scale};
        }
        return {x: x + w / 2, y: y + h / 2};
    }

    toggle() { this._menu?.toggle(); }

    /** 'idle' | 'working' | 'done' */
    setState(state, text = '', summary = '') {
        if (!this._pill || this._dead) return;
        if (this._doneTimer) { GLib.source_remove(this._doneTimer); this._doneTimer = 0; }
        this._state = state;
        for (const pc of ['working', 'done']) { this._ring.remove_style_pseudo_class(pc); this._pill.remove_style_pseudo_class(pc); }
        if (state !== 'idle') { this._ring.add_style_pseudo_class(state); this._pill.add_style_pseudo_class(state); }
        this._status.text = state === 'working' ? `Working · ${text}` : state === 'done' ? 'Done' : '';
        this._status.visible = state !== 'idle';
        this._pill.accessible_name = state === 'idle' ? 'Artemis' : `Artemis: ${this._status.text}`;
        this._stopItem.visible = state === 'working';
        // The board under the widget: what Artemis is doing, then what it did.
        this._tabTitle.text = state === 'working' ? 'WORKING' : 'DONE';
        this._tabText.text = state === 'working' ? text : summary;
        this._tabStop.visible = state === 'working';
        this._tab.visible = false; // no board: the widget goes straight back to its initial look
        if (state === 'done') this._tab.add_style_pseudo_class('done'); else this._tab.remove_style_pseudo_class('done');
        if (state === 'done') {
            this._doneTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, DONE_MS, () => {
                this._doneTimer = 0;
                if (this._state === 'done') this.setState('idle');
                return GLib.SOURCE_REMOVE;
            });
        }
        this._animate(state === 'working');
        this._wave.queue_repaint();
        this._edge.queue_repaint();
    }

    /** Show islandState.view() in the approved look, and name it for the screen reader. */
    setView(view) {
        if (!this._pill || this._dead) return;
        const look = WORKING.has(view.state) ? 'working' : view.state === 'done' ? 'done' : 'idle';
        if (look !== this._state || (look === 'working' && view.label !== this._label)) {
            this._label = view.label;
            this.setState(look, view.label);
        }
        this._pill.accessible_name = view.accessibleName;
    }

    /** The panel's Stop item: shown while a request is in progress. */
    setStopVisible(on) {
        if (this._stopItem) this._stopItem.visible = !!on;
    }

    // --- panel ---------------------------------------------------------------------------------

    _fillMenu() {
        const box = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        const column = new St.BoxLayout({vertical: true, x_expand: true, style_class: 'artemis-island-ask'});
        this._entry = new St.Entry({style_class: 'artemis-island-entry', hint_text: 'What would you like to do?', can_focus: true, x_expand: true});
        this._entry.clutter_text.connect('activate', () => this._ask(this._entry.get_text()));
        this._hint = new St.Label({style_class: 'artemis-island-hint', text: ''});
        column.add_child(this._entry);
        column.add_child(this._hint);
        box.add_child(column);
        this._menu.addMenuItem(box);
        this._menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._stopItem = this._menu.addAction('Stop', () => this._opts.onStop());
        this._stopItem.visible = false;
        this._menu.addAction('Settings…', () => this._opts.onSettings());
    }

    // Typed request: sent to the core. The field is cleared before the core answers, so a stray
    // second Enter can't send it again.
    async _ask(raw) {
        const text = raw.trim();
        if (!text) return;
        this._entry.set_text('');
        this._hint.text = 'Sending…';
        let res;
        try {
            res = await this._opts.onAsk(text);
        } catch {
            res = {ok: false, message: 'That request could not be sent. Try again.'};
        }
        if (!this._menu) return;
        if (res?.ok) {
            this._hint.text = '';
            this._menu.close();
        } else {
            this._hint.text = res?.message ?? 'That request could not be sent. Try again.';
        }
    }

    // --- layout and painting -------------------------------------------------------------------

    _layout() {
        if (!this._pill || !this._pill.width) return;
        const m = Main.layoutManager.primaryMonitor;
        if (!m) return;
        const s = this._scale;
        const panelBottom = m.y + Main.layoutManager.panelBox.height;
        if (this._state === 'idle' || !this._idleWidth) this._idleWidth = this._pill.width;
        const px = Math.round(m.x + (m.width - this._idleWidth) / 2); // the ring's slot never moves
        const py = Math.round(panelBottom + GAP * s);
        this._pill.set_position(px, py);
        if (this._tab) this._tab.set_position(px + Math.round((this._idleWidth - this._tab.width) / 2), py + this._pill.height + Math.round(8 * s)); // under the widget
        // The edge strip runs from the top of the screen (so it reserves space as a top strut) to
        // just below the pill; only the line along the bar's bottom edge is drawn in it.
        const h = Math.round(py + this._pill.height + 12 * s - m.y);
        this._edge.set_position(m.x, m.y);
        this._edge.set_size(m.width, h);
        this._geom = {lineY: panelBottom - m.y + 1.5 * s, px: px - m.x, py: py - m.y, pw: this._pill.width, ph: this._pill.height};
        this._edge.queue_repaint();
        this._opts.onMoved?.();
    }

    _edgePath(cr, side) {
        const g = this._geom, s = this._scale;
        const midY = g.py + g.ph / 2;
        if (side === 'left') {
            cr.moveTo(0, g.lineY);
            cr.lineTo(g.px - SHOULDER * s, g.lineY);
            cr.curveTo(g.px - 14 * s, g.lineY, g.px - 2 * s, midY - g.ph * 0.35, g.px + 1 * s, midY);
        } else {
            const x = g.px + g.pw;
            cr.moveTo(x - 1 * s, midY);
            cr.curveTo(x + 2 * s, midY - g.ph * 0.35, x + 14 * s, g.lineY, x + SHOULDER * s, g.lineY);
            cr.lineTo(this._edge.width, g.lineY);
        }
    }

    _paintEdge(area) {
        const cr = area.get_context();
        if (this._geom && this._drawEdge) {
            const s = this._scale;
            const strength = this._state === 'working' ? 1 : 0.85;
            // glow: wide and faint to thin and bright
            for (const [w, a] of [[16, 0.05], [9, 0.1], [5, 0.22], [2.5, 0.95 * strength]]) {
                cr.setLineWidth(w * s);
                cr.setSourceRGBA(...PURPLE, a);
                for (const side of ['left', 'right']) { this._edgePath(cr, side); cr.stroke(); }
            }
            if (this._state === 'working') this._paintPulse(cr);
        }
        cr.$dispose();
    }

    // A bead of light running out along the edge while Artemis works.
    _paintPulse(cr) {
        const g = this._geom, s = this._scale;
        const start = g.px + g.pw + SHOULDER * s, end = this._edge.width;
        const t = (this._phase * 0.35) % 1;
        const head = start + (end - start) * t;
        const tail = Math.max(start, head - 260 * s);
        const grad = new Cairo.LinearGradient(tail, 0, head, 0);
        grad.addColorStopRGBA(0, ...PURPLE, 0);
        grad.addColorStopRGBA(1, 0.95, 0.88, 1, 0.95);
        cr.setSource(grad);
        cr.setLineWidth(5 * s);
        cr.setLineCap(Cairo.LineCap.ROUND);
        cr.moveTo(tail, g.lineY);
        cr.lineTo(head, g.lineY);
        cr.stroke();
        for (const [r, a] of [[14, 0.12], [8, 0.3], [4, 1]]) {
            cr.setSourceRGBA(0.96, 0.9, 1, a);
            cr.arc(head, g.lineY, r * s, 0, 2 * Math.PI);
            cr.fill();
        }
    }

    // Style B: mirrored bars. Short and quiet at rest; alive while working.
    _paintWave(area) {
        const cr = area.get_context();
        const [w, h] = area.get_surface_size();
        const active = this._state === 'working';
        const n = 8, bw = 3 * this._scale, gap = (w - n * bw) / (n - 1);
        cr.setSourceRGBA(0.85, 0.75, 0.99, active ? 0.95 : 0.55);
        for (let i = 0; i < n; i++) {
            const rest = (i % 2 ? 6 : 4) * this._scale;
            const live = h * (0.25 + 0.7 * Math.abs(Math.sin(this._phase * 5 + i * 1.3) * Math.sin(this._phase * 2.1 + i * 0.7)));
            const bh = active ? Math.max(rest, live) : rest;
            const x = i * (bw + gap), y = (h - bh) / 2, r = bw / 2;
            cr.newSubPath();
            cr.arc(x + r, y + r, r, Math.PI, 0);
            cr.arc(x + r, y + bh - r, r, 0, Math.PI);
            cr.closePath();
            cr.fill();
        }
        cr.$dispose();
    }

    _animate(on) {
        if (on && !this._timer) {
            let last = GLib.get_monotonic_time();
            this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 33, () => {
                const now = GLib.get_monotonic_time();
                this._phase += (now - last) / 1e6;
                last = now;
                this._wave?.queue_repaint();
                this._edge?.queue_repaint();
                return GLib.SOURCE_CONTINUE;
            });
        } else if (!on && this._timer) {
            GLib.source_remove(this._timer);
            this._timer = 0;
        }
    }
}
