// The Artemis top-edge island: a glowing pill hanging under the top bar, joined to a light line
// along the bottom edge of the bar, with the ring, the "artemis." name, a waveform and a chevron.
//   - the chevron (or the waveform) opens a panel: type what you want, or pick a command
//   - while a command runs the pill says so, the waveform moves and a pulse of light runs along
//     the edge; when it ends the ring shows a check for a few seconds
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

const words = (s) => s.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length > 2);

/** 'wh' for a wh-question, 'aux' for one that starts like "can you" or "is it", else null. */
function questionKind(text) {
    const t = text.trim().toLowerCase().replace(/^(hey |ok |okay )?artemis[,.]?\s*/, '');
    if (/^(what|why|how|when|where|who|whom|whose|which)\b/.test(t)) return 'wh';
    if (/^(can|could|would|will|should|is|are|am|was|were|do|does|did|have|has|may|might|shall)\b/.test(t)) return 'aux';
    if (/\?\s*$/.test(t)) return 'aux';
    return null;
}

export class Island {
    /**
     * opts: {commands, onRun(cmd), onStop(), onSettings(), onMoved()}
     */
    constructor(opts) {
        this._opts = opts;
        this._state = 'idle';
        this._phase = 0;
        this._scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;

        // The light along the edge (non-reactive: clicks pass through to the top bar and windows).
        this._edge = new St.DrawingArea({reactive: false});
        this._edge.connect('repaint', (area) => this._paintEdge(area));
        Main.layoutManager.addChrome(this._edge, {affectsStruts: true, trackFullscreen: true});

        // The pill.
        this._pill = new St.BoxLayout({style_class: 'artemis-island', reactive: true, y_align: Clutter.ActorAlign.CENTER});
        this._ring = new St.Widget({style_class: 'artemis-island-ring', y_align: Clutter.ActorAlign.CENTER});
        this._name = new St.Label({style_class: 'artemis-island-name', text: 'artemis.', y_align: Clutter.ActorAlign.CENTER});
        this._wave = new St.DrawingArea({style_class: 'artemis-island-wave'});
        this._wave.connect('repaint', (area) => this._paintWave(area));
        this._status = new St.Label({style_class: 'artemis-island-status', text: '', y_align: Clutter.ActorAlign.CENTER, visible: false});
        // Push to talk: hold the bars to speak, release to send. From the keyboard, Space or Enter on
        // them starts and stops (and Ctrl+Shift+Space works anywhere, see extension.js).
        this._waveButton = new St.Button({style_class: 'artemis-island-wave-button', can_focus: true, accessible_name: 'Push to talk', child: this._wave});
        this._waveButton.connect('button-press-event', () => { this._opts.onTalk?.(true); return Clutter.EVENT_STOP; });
        this._waveButton.connect('button-release-event', () => { this._opts.onTalk?.(false); return Clutter.EVENT_STOP; });
        this._waveButton.connect('key-press-event', (_a, e) => {
            const k = e.get_key_symbol();
            if (k !== Clutter.KEY_space && k !== Clutter.KEY_Return) return Clutter.EVENT_PROPAGATE;
            this._opts.onTalk?.(!this._listening);
            return Clutter.EVENT_STOP;
        });
        const chevronIcon = new St.Icon({icon_name: 'pan-down-symbolic', style_class: 'artemis-island-chevron-icon'});
        this._chevron = new St.Button({style_class: 'artemis-island-chevron', can_focus: true, accessible_name: 'Open Artemis', child: chevronIcon});
        this._chevron.connect('clicked', () => this.toggle());
        const sep = () => new St.Widget({style_class: 'artemis-island-sep', y_align: Clutter.ActorAlign.CENTER});
        for (const child of [this._ring, sep(), this._waveButton, this._chevron]) this._pill.add_child(child);
        // The glow: its own layer just behind the pill (the pill's frosted blur would clip a shadow drawn on it).
        this._glowActor = new St.Widget({style_class: 'artemis-island-glow', opacity: 0, reactive: false});
        Main.layoutManager.addChrome(this._glowActor, {trackFullscreen: true});
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
        // The menu wears the widget's material: the same frost and the same colour (CSS).
        try {
            const mblur = new Shell.BlurEffect({mode: Shell.BlurMode.BACKGROUND, brightness: 0.75});
            if ('radius' in mblur) mblur.radius = 24; else mblur.sigma = 12;   // the same frost as the widget
            if ('corner_radius' in mblur) mblur.corner_radius = 0;   // square, like the panel
            this._menu.box.add_effect(mblur);
        } catch (e) { console.warn(`Artemis: no menu blur (${e.message})`); }
        this._menu.connect('open-state-changed', (_m, open) => {
            this._glass(open);
            chevronIcon.icon_name = open ? 'pan-up-symbolic' : 'pan-down-symbolic';
            this._chevron.accessible_name = open ? 'Close Artemis' : 'Open Artemis';
            if (open) GLib.idle_add(GLib.PRIORITY_DEFAULT, () => { this._recentBox?.get_first_child()?.grab_key_focus(); return GLib.SOURCE_REMOVE; });
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
        if (this._glassTimer) GLib.source_remove(this._glassTimer);
        this._glassTimer = 0;
        this._glassOff();
        this._menu?.destroy();
        this._tab?.destroy();
        for (const actor of [this._pill, this._edge]) {
            if (!actor) continue;
            Main.layoutManager.removeChrome(actor);
            actor.destroy();
        }
        this._glowActor?.destroy();
        this._glowActor = null;
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

    // --- liquid glass under the menu (glassEffect.js on each monitor's wallpaper) ----------------

    _glass(open) {
        if (this._glassTimer) { GLib.source_remove(this._glassTimer); this._glassTimer = 0; }
        const box = this._menu?.box;
        if (!open) {
            if (box) { box.translation_y = 0; box.scale_x = box.scale_y = 1; box.get_children().forEach((c) => { c.opacity = 255; c.translation_y = 0; }); }
            return;
        }
        // Liquid open: the menu drops out of the pill, sinks a little past its place, rises back and
        // settles (an underdamped spring), and the items ripple in one by one.
        const items = box ? box.get_children() : [];
        if (box) { box.set_pivot_point(0.5, 0); }
        const spring = (t, z = 0.42, w = 15) => {
            if (t <= 0) return 0;
            const wd = w * Math.sqrt(1 - z * z);
            return 1 - Math.exp(-z * w * t) * (Math.cos(wd * t) + (z * w / wd) * Math.sin(wd * t));
        };
        const t0 = GLib.get_monotonic_time();
        this._glassTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 16, () => {
            const t = (GLib.get_monotonic_time() - t0) / 1e6;
            const p = spring(t);
            if (box) {
                box.translation_y = -18 * (1 - p);          // overshoot (p > 1) sinks it below its place
                box.scale_y = 0.9 + 0.1 * p;                 // and stretches it a touch, like a drop
                box.scale_x = 1 - 0.04 * (p - 1);            // thinner while stretched, wider while squashed
                items.forEach((c, i) => {
                    const q = Math.min(Math.max(spring(t - 0.05 - i * 0.025, 0.6, 18), 0), 1.2);
                    c.opacity = Math.round(255 * Math.min(q, 1));
                    c.translation_y = 8 * (1 - q);
                });
            }
            if (t < 1.1) return GLib.SOURCE_CONTINUE;
            if (box) { box.translation_y = 0; box.scale_x = box.scale_y = 1; items.forEach((c) => { c.opacity = 255; c.translation_y = 0; }); }
            this._glassTimer = 0;
            return GLib.SOURCE_REMOVE;
        });
    }

    _glassOff() {
        for (const [actor, fx] of this._glassFx ?? []) {
            try { actor.remove_effect(fx); actor.queue_redraw(); } catch (_) { /* the wallpaper was replaced */ }
        }
        this._glassFx = null;
    }

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
        this._animate(this._wantAnim());
        this._wave.queue_repaint();
        this._edge.queue_repaint();
    }

    // --- panel ---------------------------------------------------------------------------------

    // Two panes: on the left, the request box and your recent prompts; on the right, what Artemis is
    // doing for the selected prompt as a process tree (each step done, running or stopped), which
    // becomes a short summary when the task ends.
    _fillMenu() {
        this._tasks = this._tasks ?? [];
        const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false, style_class: 'artemis-island-two-item'});
        const panes = new St.BoxLayout({style_class: 'artemis-island-two', x_expand: true});

        // No text box: requests are spoken (push to talk). The left pane is only the recent prompts.
        const left = new St.BoxLayout({vertical: true, style_class: 'artemis-island-left'});
        this._recentTitle = new St.Label({style_class: 'artemis-island-pane-title', text: 'Recent'});
        this._recentBox = new St.BoxLayout({vertical: true, style_class: 'artemis-island-recent'});
        for (const c of [this._recentTitle, this._recentBox]) left.add_child(c);

        const right = new St.BoxLayout({vertical: true, style_class: 'artemis-island-right', x_expand: true});
        this._dGoal = new St.Label({style_class: 'artemis-island-pane-title', text: 'Goal'});
        this._dTitle = new St.Label({style_class: 'artemis-island-d-title'});
        this._dTitle.clutter_text.line_wrap = true;
        this._dStatus = new St.Label({style_class: 'artemis-island-d-status'});
        this._dTree = new St.BoxLayout({vertical: true, style_class: 'artemis-island-tree'});
        this._dSummary = new St.Label({style_class: 'artemis-island-d-summary'});
        this._dSummary.clutter_text.line_wrap = true;
        const actions = new St.BoxLayout({style_class: 'artemis-island-d-actions'});
        this._stopItem = {visible: false}; // no Stop button in the panel (stop by voice or from the Artemis menu)
        this._againBtn = new St.Button({style_class: 'artemis-island-d-btn artemis-island-d-btn-main', label: 'Run again', can_focus: true, visible: false});
        this._againBtn.connect('clicked', () => { const t = this._selected(); if (t?.cmd) { this._menu.close(); this._opts.onRun(t.cmd, t.prompt); } });
        actions.add_child(this._againBtn);
        // An approval from the core: what will change, then Reject / Approve (Enter approves, Escape rejects).
        this._apprBox = new St.BoxLayout({vertical: true, style_class: 'artemis-island-appr', visible: false});
        this._apprText = new St.Label({style_class: 'artemis-island-appr-text'});
        this._apprText.clutter_text.line_wrap = true;
        const apprRow = new St.BoxLayout({style_class: 'artemis-island-d-actions'});
        this._rejectBtn = new St.Button({style_class: 'artemis-island-d-btn', label: 'Reject', can_focus: true});
        this._approveBtn = new St.Button({style_class: 'artemis-island-d-btn artemis-island-d-btn-main', label: 'Approve', can_focus: true});
        this._rejectBtn.connect('clicked', () => this._decide(false));
        this._approveBtn.connect('clicked', () => this._decide(true));
        for (const b of [this._rejectBtn, this._approveBtn]) b.connect('key-press-event', (_a, e) => {
            if (e.get_key_symbol() === Clutter.KEY_Escape) { this._decide(false); return Clutter.EVENT_STOP; }
            return Clutter.EVENT_PROPAGATE;
        });
        apprRow.add_child(this._rejectBtn); apprRow.add_child(this._approveBtn);
        this._apprBox.add_child(this._apprText); this._apprBox.add_child(apprRow);
        for (const c of [this._dGoal, this._dTitle, this._dStatus, this._dTree, this._apprBox, this._dSummary, actions]) right.add_child(c);

        panes.add_child(left);
        panes.add_child(new St.Widget({style_class: 'artemis-island-vsep'}));
        panes.add_child(right);
        item.add_child(panes);
        this._menu.addMenuItem(item);
        // one quiet footer line: how to talk, and Settings
        const foot = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false, style_class: 'artemis-island-foot-item'});
        const fbox = new St.BoxLayout({x_expand: true, style_class: 'artemis-island-foot'});
        // the trademark, small, bottom left: artemis. with the period in the accent
        const mark = new St.Label({style_class: 'artemis-island-mark', y_align: Clutter.ActorAlign.CENTER});
        mark.clutter_text.set_markup('artemis<span foreground="#a99fc7">.</span>');
        fbox.add_child(mark);
        this._footHint = new St.Label({style_class: 'artemis-island-foot-hint', text: 'Hold the bars or Ctrl+Shift+Space to talk', x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        const set = new St.Button({style_class: 'artemis-island-foot-btn', label: 'Settings', can_focus: true});
        set.connect('clicked', () => { this._menu.close(); this._opts.onSettings(); });
        fbox.add_child(this._footHint); fbox.add_child(set);
        foot.add_child(fbox);
        this._menu.addMenuItem(foot);
        this._renderTasks();
    }

    // --- tasks: recent prompts and the process tree ----------------------------------------------

    /** A prompt starts. Anything still running was replaced by it. */
    taskStart(prompt, cmd, id = null) {
        this._tasks = this._tasks ?? [];
        for (const t of this._tasks) if (t.state === 'working') { t.state = 'stopped'; t.summary = 'Replaced by a newer request.'; t.steps.forEach((x) => { if (x.state === 'running') x.state = 'stopped'; }); }
        const task = {prompt, cmd, id, state: 'working', steps: [], summary: '', at: GLib.DateTime.new_now_local()};
        this._tasks.unshift(task);
        this._tasks.length = Math.min(this._tasks.length, 6);
        this._sel = task;
        this._renderTasks();
    }

    /** The running prompt moves to its next step. */
    taskStep(label) {
        const t = this._tasks?.find((x) => x.state === 'working');
        if (!t) return;
        t.steps.forEach((x) => { if (x.state === 'running') x.state = 'done'; });
        t.steps.push({label, state: 'running'});
        this._renderTasks();
    }

    /** The core asks to approve a change or a run: show it and put the focus on Approve. */
    taskApproval(id, approval) {
        const t = this._tasks?.find((x) => x.id === id) ?? this._tasks?.find((x) => x.state === 'working');
        if (!t) return;
        t.approval = approval;
        this._sel = t;
        if (!this._menu.isOpen) this._menu.open();
        this._renderTasks();
        GLib.idle_add(GLib.PRIORITY_DEFAULT, () => { this._approveBtn?.grab_key_focus(); return GLib.SOURCE_REMOVE; });
    }

    taskApprovalResolved(id) {
        const t = this._tasks?.find((x) => x.approval?.approval_id === id);
        if (!t) return;
        t.approval = null;
        this._renderTasks();
    }

    _decide(approve) {
        const t = this._selected();
        if (!t?.approval) return;
        const a = t.approval;
        t.approval = null; // one decision per approval; the core confirms with approval.resolved
        this._renderTasks();
        this._opts.onApprove?.(a, approve);
    }

    /** Find a task by the core's id, for events that carry task_id. */
    taskById(id) { return this._tasks?.find((x) => x.id === id) ?? null; }

    /** Live detail of the running step: where the cursor is going, what has been typed so far. */
    taskProgress(detail) {
        const t = this._tasks?.find((x) => x.state === 'working');
        const step = t?.steps.findLast((x) => x.state === 'running');
        if (!step) return;
        step.detail = detail;
        if (this._liveLabel && this._selected() === t) {
            this._liveLabel.text = detail;
            this._liveLabel.visible = !!detail;
        }
    }

    /** The prompt ended: the tree closes and the summary takes over. */
    taskEnd(summary, failed) {
        const t = this._tasks?.find((x) => x.state === 'working');
        if (!t) return;
        t.state = failed ? 'stopped' : 'done';
        t.steps.forEach((x) => { if (x.state === 'running') x.state = failed ? 'stopped' : 'done'; });
        t.summary = summary;
        this._renderTasks();
    }

    _selected() { return this._tasks?.includes(this._sel) ? this._sel : this._tasks?.[0] ?? null; }

    _renderTasks() {
        if (!this._recentBox) return;
        const GLYPH = {working: '\u25CF', done: '\u2713', stopped: '!'};
        this._recentBox.destroy_all_children();
        const sel = this._selected();
        this._recentTitle.text = 'Recent';
        if (!this._tasks.length) this._recentBox.add_child(new St.Label({style_class: 'artemis-island-empty', text: 'Nothing yet'}));
        for (const t of this._tasks) {
            const row = new St.BoxLayout({style_class: 'artemis-island-row-in'});
            const g = new St.Label({style_class: `artemis-island-glyph artemis-island-glyph-${t.state}`, text: GLYPH[t.state], y_align: Clutter.ActorAlign.CENTER});
            const l = new St.Label({text: t.prompt, x_expand: true, y_align: Clutter.ActorAlign.CENTER});
            l.clutter_text.ellipsize = 3; // Pango.EllipsizeMode.END
            row.add_child(g); row.add_child(l);
            const b = new St.Button({style_class: 'artemis-island-row', child: row, can_focus: true, x_align: Clutter.ActorAlign.FILL,
                accessible_name: `${t.prompt}, ${t.state === 'working' ? 'working' : t.state === 'done' ? 'done' : 'stopped'}`});
            if (t === sel) b.add_style_pseudo_class('checked');
            b.connect('clicked', () => { this._sel = t; this._renderTasks(); });
            this._recentBox.add_child(b);
        }
        // the right pane
        this._dTree.destroy_all_children();
        this._liveLabel = null;
        if (this._listening || this._hearing) {
            const said = this._partial?.trim();
            this._dGoal.text = 'Goal';
            this._dTitle.text = said ? `${said} \u258D` : (this._listening ? 'Listening\u2026' : 'Transcribing\u2026');
            this._dStatus.text = this._listening ? 'Listening \u00b7 stops when you pause' : 'Got it\u2026';
            if (this._listening) this._dStatus.add_style_pseudo_class('working'); else this._dStatus.remove_style_pseudo_class('working');
            this._dSummary.text = ''; this._dSummary.visible = false;
            this._stopItem.visible = this._againBtn.visible = false;
            return;
        }
        if (!sel) {
            this._dTitle.text = 'Say what you want done';
            this._dStatus.text = '';
            this._dSummary.visible = true;
            this._dSummary.text = 'Your request becomes the goal here, with each step as Artemis works.';
            this._stopItem.visible = this._againBtn.visible = false;
            return;
        }
        this._dTitle.text = sel.prompt;
        this._dGoal.text = sel.kind === 'question' ? 'Question' : 'Goal';
        this._dStatus.text = sel.state === 'working' ? 'Working\u2026' : `${sel.state === 'done' ? 'Done' : 'Stopped'} \u00b7 ${sel.at.format('%H:%M')}`;
        if (sel.state === 'working') this._dStatus.add_style_pseudo_class('working'); else this._dStatus.remove_style_pseudo_class('working');
        const steps = sel.steps.length ? sel.steps : (sel.state === 'working' ? [{label: 'Starting', state: 'running'}] : []);
        steps.forEach((x, i) => {
            const row = new St.BoxLayout({style_class: 'artemis-island-tree-row'});
            const branch = new St.Label({style_class: 'artemis-island-branch', text: i === steps.length - 1 ? '\u2514' : '\u251C'});
            const g = new St.Label({style_class: `artemis-island-glyph artemis-island-glyph-${x.state === 'running' ? 'working' : x.state}`, text: x.state === 'running' ? '\u25CF' : x.state === 'done' ? '\u2713' : '!'});
            const col = new St.BoxLayout({vertical: true, x_expand: true});
            const l = new St.Label({style_class: `artemis-island-step artemis-island-step-${x.state}`, text: x.label});
            l.clutter_text.line_wrap = true;
            col.add_child(l);
            if (x.state === 'running') {
                // updated in place as the cursor moves or text is typed (taskProgress), without rebuilding the tree
                this._liveLabel = new St.Label({style_class: 'artemis-island-step-live', text: x.detail ?? '', visible: !!x.detail});
                col.add_child(this._liveLabel);
            }
            row.add_child(branch); row.add_child(g); row.add_child(col);
            this._dTree.add_child(row);
        });
        this._apprBox.visible = !!sel.approval;
        if (sel.approval) this._apprText.text = sel.approval.summary.replace(/\s*Enter to (approve|run), Escape to reject\.?\s*$/i, '');
        this._dSummary.text = sel.state === 'working' ? '' : sel.summary;
        this._dSummary.visible = sel.state !== 'working';
        this._stopItem.visible = sel.state === 'working';
        this._againBtn.visible = sel.state !== 'working' && !!sel.cmd;
    }

    /** Push to talk: the bars move while listening, and the right pane says so. */
    setListening(listening, hearing = false) {
        if (!listening && !hearing) this._partial = '';
        this._listening = listening;
        this._hearing = hearing;
        this._animate(this._wantAnim());
        this._wave?.queue_repaint();
        if (listening && !this._menu.isOpen) this._menu.open();
        this._renderTasks();
    }

    /** Your words so far, printed live while you speak (local Whisper). */
    partial(text) {
        this._partial = text;
        if (!this._listening) this.setListening(true); else this._renderTasks();
    }

    /** What the developer said: a request becomes the goal; a question is recognised as one. */
    hear(text) {
        this._hearing = false;
        this._partial = '';
        if (!text) { this._renderTasks(); return; }
        const q = questionKind(text);
        // "Can you open VS Code?" is a request phrased as a question: run it if it matches a command.
        if (q === 'wh' || (q === 'aux' && !this._match(text))) {
            this.taskStart(text.trim(), null);
            this._tasks[0].kind = 'question';
            this.taskEnd('Recognised as a question. Answers aren\u2019t connected yet: ask Artemis to do something instead.', false);
            this._questionUntil = GLib.get_monotonic_time() + 6e6; // the glow holds while Artemis replies
            this._animate(this._wantAnim());
            this._opts.onQuestion?.(text.trim());
            return;
        }
        this._ask(text);
    }

    _match(text) {
        const want = words(text);
        let best = null, score = 0;
        for (const cmd of this._opts.commands) {
            const have = words(`${cmd.name} ${(cmd.keywords ?? []).join(' ')}`);
            const s = want.filter((w) => have.some((h) => h.startsWith(w) || w.startsWith(h))).length;
            if (s > score) { score = s; best = cmd; }
        }
        return score >= 2 ? best : null;
    }

    _questioning() { return (this._questionUntil ?? 0) > GLib.get_monotonic_time(); }

    _wantAnim() { return this._state === 'working' || !!this._speaking || !!this._listening || this._questioning(); }

    /** A soft lilac glow that breathes on the pill while Artemis listens, or after a question. */
    _glowTick() {
        const on = !!this._listening || this._questioning();
        if (!on) { if (this._glowing) { this._pill?.set_style(null); if (this._glowActor) this._glowActor.opacity = 0; this._glowing = false; } return; }
        this._glowing = true;
        const b = 0.5 + 0.5 * Math.sin(this._phase * (this._listening ? 4.2 : 2.6));
        if (this._glowActor) {
            if (this._glowActor.width !== this._pill.width) this._glowActor.set_size(this._pill.width, this._pill.height);
            this._glowActor.opacity = Math.round(150 + 105 * b);   // breathes
        }
        this._pill?.set_style(`border-color: rgba(190, 180, 225, ${(0.45 + 0.4 * b).toFixed(2)});`);
    }

    // Spoken request: run the command it best matches; otherwise keep it as a goal Artemis could not do.
    _ask(text) {
        const want = words(text);
        let best = null, score = 0;
        for (const cmd of this._opts.commands) {
            const have = words(`${cmd.name} ${(cmd.keywords ?? []).join(' ')}`);
            const s = want.filter((w) => have.some((h) => h.startsWith(w) || w.startsWith(h))).length;
            if (s > score) { score = s; best = cmd; }
        }
        if (!best) {
            this.taskStart(text.trim(), null);
            this.taskEnd('I can\u2019t do that yet. Try other words.', true);
            this._opts.onUnknown?.(text.trim());
            return;
        }
        this._opts.onRun(best, text.trim()); // the menu stays open: the right pane shows the work as it happens
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
        this._glowActor?.set_position(px, py);
        this._glowActor?.set_size(this._pill.width, this._pill.height);
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
        const active = this._state === 'working' || this._speaking || this._listening;
        const n = 8, bw = 3 * this._scale, gap = (w - n * bw) / (n - 1);
        cr.setSourceRGBA(0.83, 0.83, 0.85, active ? 0.8 : 0.45); // graphite bars
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

    /** The bars move while Artemis speaks, too. */
    setSpeaking(on) {
        this._speaking = on;
        this._animate(this._wantAnim());
        this._wave?.queue_repaint();
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
                this._glowTick();
                if (!this._wantAnim()) { this._timer = 0; this._glowTick(); return GLib.SOURCE_REMOVE; }
                return GLib.SOURCE_CONTINUE;
            });
        } else if (!on && this._timer) {
            GLib.source_remove(this._timer);
            this._timer = 0;
        }
    }
}
