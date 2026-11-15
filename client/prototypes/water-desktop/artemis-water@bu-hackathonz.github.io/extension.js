// Artemis: liquid glass for the GNOME desktop.
//
// Driver (setting "driver"):
//   agent (default): Artemis is a small liquid-glass sphere resting in the bottom-left corner. Your
//                    mouse doesn't stir anything. Pick a command (top-bar button, or click the
//                    sphere) and the sphere zips through it: it refracts whatever it passes over,
//                    drags a water wake behind it, rings the water at each step, then flies home.
//   pointer:         the original cursor effects. Style "lens" (a glass lens follows the pointer)
//                    or "water" (the pointer drags ripples through the screen).
//
// Rendering: Shell.GLSLEffects on GNOME's top-level UI group (or only the wallpaper, see
// "cover-everything"): lensEffect.js draws the glass sphere/lens, waterEffect.js the wake. Both
// only run while something moves; a resting sphere is a single static frame.

import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import Shell from 'gi://Shell';
import Gst from 'gi://Gst?version=1.0';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {WaterEffect, MAX_DROPS} from './waterEffect.js';
import {LensEffect} from './lensEffect.js';
import {OrbEffect} from './orbEffect.js';
import {RingEffect} from './ringEffect.js';
import {Island} from './island.js';
import {CaretTracker} from './caret.js';
import {CommandRunner, COMMANDS} from './presets.js';
import {Voice} from './voice.js';
import {VOICES, voiceByKey} from './voices.js';
import {Listener} from './listen.js';
import {WakeWord} from './wakeword.js';
import {CoreClient} from './core.js';

// Loop.
const ACTIVE_MS = 16;           // frame interval while anything moves
const IDLE_MS = 60;             // pointer polling while still (pointer driver)
const AGENT_IDLE_MS = 250;      // orb frozen at rest: only watch for overview/fullscreen changes
const ORB_LIVE_MS = 33;         // orb at rest but alive (slow morph and tumble): ~30 fps

// Water (how the wake answers speed).
const MIN_SPEED = 60;           // px/s: slower movement makes no water
const SPEED_REF = 250;          // px/s over which the push rises from its floor toward its maximum
const PUSH_FLOOR = 0.3;         // share of the push a deliberate slow drift always gets
const MAX_JUMP = 400;           // px in one poll: a warp, not a stroke
const DROP_MIN_MS = 30;         // at most ~33 drops per second
const PUSH = 0.5;               // largest push (slope units) a fast sweep can make
const CLICK_PUSH = 0.3;         // a click (or a command's tap) drops one ring
const RING_SPEED = 150;         // px/s the ripple fronts travel
const BEND_GAIN = 30;           // px of bending per unit slope at distortion 1
const BEND_CAP = 10;            // px: the most anything is ever shifted at distortion 1
const DISPERSION = 0.12;        // spectral spread (rainbow fringes on steep fronts)
const SLOW_SPEED = 200;         // px/s at or below which the water is fully "slow"
const FAST_SPEED = 1200;        // px/s at or above which it is fully "fast"
const FORM_SLOW = {ringSpeed: 90, ringWidth: 1.0, wake: 1.8, reach: 0.8, spacing: 9};
const FORM_FAST = {ringSpeed: 230, ringWidth: 1.4, wake: 0.6, reach: 1.35, spacing: 20};

// Lens / sphere.
const LENS_STIFFNESS = 320;     // spring pulling the lens after its target (1/s²)
const LENS_DAMPING = 30;        // damping ratio ≈ 0.84
const LENS_SHOW_FOR = 1.5;      // pointer driver: s the lens stays after the pointer stops
const LENS_FADE = 0.12;         // s time constant of fade in/out
const LENS_MAX_STRETCH = 0.3;   // longest it gets along a fast move (1.3x)
const LENS_STRETCH_SPEED = 2500; // px/s for full stretch
const LENS_DISPERSION = 0.12;   // spectral spread at the rim
const HOME_MARGIN = 22;         // px between the resting sphere and the work-area corner

// Agent orb (see orbEffect.js).
const ORB_DISPERSION = 0.10;
const ORB_POP_K = 180;          // scale spring: stiffness (1/s²) ...
const ORB_POP_D = 12;           // ... and damping (ratio ≈ 0.45: a jelly-like overshoot)
const ORB_ENERGY_DECAY = 1.4;   // 1/s: how fast a wobble calms down
const RING_GLOW = 0.32;         // the ring's resting halo
const RING_FLASH_GLOW = 0.7;    // extra halo when clicked (fades out)
const RING_FLASH_DECAY = 1.8;   // 1/s (the glow and the pressed wobble fade together)
const CLICK_FLASH = 0.6;        // how hard a click splashes (1 = the old, wilder splash)
const HOLD_WOBBLE = 0.32;       // menu open: the steady jiggle ...
const HOLD_GLOW = 0.4;          // ... and the extra glow it keeps
const HOLD_EASE = 5;            // 1/s: easing into and out of the held state
const HOLD_POKE_MS = [380, 900]; // menu open: random little pokes to its jelly spring, this often
const MORPH_SECONDS = 0.55;     // ring <-> droplet transition (a command waits for it to start)
const TRAIL_HIGHLIGHTS = 1.6;   // agent trail: brighter glossy lines ...
const TRAIL_DISPERSION = 2.0;   // ... stronger rainbow fringes ...
const TRAIL_GLOW = 0.08;        // ... and a faint glow, so it's easy to follow
const TRAIL_TINT = [0.7, 0.42, 1.0]; // the agent's purple

// The orb's trail: a short, thin groove in the water right behind it (not a wide wake).
const TRAIL_LIFE = 0.45;        // s a bit of trail lasts
const TRAIL_SPACING = 6;        // px between trail points
const TRAIL_MIN_MS = 14;        // at most one trail point per frame
const TRAIL_DEPTH = 8;          // overall strength
const DRAG_THRESHOLD = 5;       // px the pointer must move before a press becomes a drag

const mix = (a, b, t) => a + (b - a) * t;
const smooth = (t) => { t = Math.min(Math.max(t, 0), 1); return t * t * (3 - 2 * t); };
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export default class ArtemisWaterExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._interface = St.Settings.get();
        this._drops = [];
        this._last = null;          // last sample {x, y, t}
        this._lastDrop = null;
        this._buttonDown = false;
        this._effects = new Map();  // actor -> {water, lens, id}
        this._waterOn = false;
        this._lensOn = false;
        this._lens = null;
        this._agent = null;

        this._attachAll();
        this._monitorsId = Main.layoutManager.connect('monitors-changed', () => this._reattachSoon());
        this._bgAddedId = Main.layoutManager._backgroundGroup.connect('child-added', () => {
            if (!this._coverAll()) this._reattachSoon();
        });
        this._homeId = this._settings.connect('changed::orb-home', () => { if (!this._drag) this._agentGoHome(false); });
        this._settingIds = ['cover-everything', 'style', 'driver', 'orb-size', 'agent-look', 'ui-font'].map((k) =>
            this._settings.connect(`changed::${k}`, () => { this._drops = []; this._lens = null; this._orb = null; this._morph = null; this._setupAgent(); this._reattachSoon(); }));
        this._workAreaId = global.display.connect('workareas-changed', () => this._agentGoHome(true));

        // Artemis's voice, as in main: ElevenLabs (Artemis's own voice), else a stock voice, else the system voice
        this._voice = new Voice((speaking) => { this._island?.setSpeaking(speaking); this._wakeWord?.setMuted(speaking || !!this._listener?.listening); }, () => this._settings?.get_string('voice') ?? 'bill');
        this._voiceSettingId = this._settings.connect('changed::voice', () => {
            this._voiceItems?.forEach(([k, item]) => item.setOrnament(k === this._settings.get_string('voice') ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE));
            const v = voiceByKey(this._settings.get_string('voice'));
            this._voice?.say(`This is ${v.name}. I will read your code and results like this.`, 'high'); // a preview of the new voice
        });
        // Push to talk: hold the island's bars, or press Ctrl+Shift+Space to start and again to send.
        this._listener = new Listener();
        this._voice.quiet = () => !!this._core?.connected; // main's core speaks for Artemis while it runs
        // Hands-free: listen for "Hey Artemis" (opt-in, local Whisper; see wake.py)
        this._wakeWord = new WakeWord(this.path, {
            onArmed: () => { if (!this._island?._listening) this._earcon('listen-on'); this._island?.setListening(true); },
            onSpeech: () => { this._island?.setListening(true); },
            onPartial: (text) => { this._island?.partial(text); },          // your words, live, as you speak
            onCancel: () => {
                // "cancel", "stop", "never mind": stop listening, stop talking, and stop the task under way
                this._localTalking = false;
                this._earcon('listen-off');
                this._island?.setListening(false);
                this._voice?.stop();
                if (this._island?._tasks?.some((t) => t.state === 'working')) this._stopTask();
                this._voice?.say('Cancelled.', 'normal');
            },
            onDisarmed: () => { this._localTalking = false; this._earcon('listen-off'); this._island?.setListening(false); },
            onRequest: (text) => { this._localTalking = false; this._earcon('listen-off'); this._island?.setListening(false); this._island?.hear(text); },
            onError: (msg) => { console.warn(`Artemis wake word: ${msg}`); this._island?.setListening(false); },
        });
        // While main's core runs it owns the microphone (its own push to talk and wake word): the island's listener stays off.
        // In local mode the helper also serves push to talk (live words, stops on silence), so it runs whenever the
        // core does not; the setting only decides whether saying the name starts a request.
        const wakeOn = () => {
            if (this._core?.connected || !WakeWord.available()) { this._wakeWord.stop(); return; }
            this._wakeWord.setWake(this._settings.get_boolean('wake-word'));
            this._wakeWord.start();
        };
        this._wakeOn = wakeOn;
        this._wakeSettingId = this._settings.connect('changed::wake-word', () => {
            wakeOn();
            this._wakeItem?.setToggleState(this._settings.get_boolean('wake-word'));
            this._voice?.say(this._settings.get_boolean('wake-word') ? 'Listening for Hey Artemis.' : 'Stopped listening for my name.', 'normal');
        });
        wakeOn();
        // Main's core (infra/start.sh): when it runs, the island is its client (see core.js)
        this._core = new CoreClient({
            onConnected: (on) => this._coreConnected(on),
            onEvent: (ev) => this._coreEvent(ev),
        });
        Main.wm.addKeybinding('push-to-talk', this._settings, Meta.KeyBindingFlags.IGNORE_AUTOREPEAT,
            Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW, () => this._talk(this._core?.connected ? !this._coreTalking : this._wakeWord?.running ? !this._localTalking : !this._listener?.listening));
        this._runner = new CommandRunner(this._agentBody(), (x, y, strength) => this.ripple(x, y, strength));
        this._addPanelButton();
        this._setupAgent();
        this._schedule(IDLE_MS);
    }

    disable() {
        this._runner?.destroy();
        this._caret?.destroy();
        this._caret = null;
        this._runner = null;
        Main.wm.removeKeybinding('push-to-talk');
        this._core?.destroy();
        this._core = null;
        if (this._wakeSettingId) this._settings?.disconnect(this._wakeSettingId);
        this._wakeSettingId = 0;
        this._wakeWord?.stop();
        this._wakeWord = null;
        this._wakeItem = null;
        this._listener?.destroy();
        this._listener = null;
        this._voice?.destroy();
        this._voice = null;
        if (this._voiceSettingId) this._settings?.disconnect(this._voiceSettingId);
        this._voiceSettingId = 0;
        this._voiceItems = null;
        this._teardownAgentUI();
        this._button?.destroy();
        this._button = null;
        this._stopItems = [];
        if (this._timer) GLib.source_remove(this._timer);
        this._timer = 0;
        if (this._homeTimer) GLib.source_remove(this._homeTimer);
        this._homeTimer = 0;
        if (this._dragTimer) GLib.source_remove(this._dragTimer);
        this._dragTimer = 0;
        this._drag = null;
        if (this._homeId) this._settings.disconnect(this._homeId);
        this._homeId = 0;
        if (this._reattachId) GLib.source_remove(this._reattachId);
        this._reattachId = 0;
        if (this._monitorsId) Main.layoutManager.disconnect(this._monitorsId);
        if (this._bgAddedId) Main.layoutManager._backgroundGroup.disconnect(this._bgAddedId);
        for (const id of this._settingIds ?? []) this._settings.disconnect(id);
        if (this._workAreaId) global.display.disconnect(this._workAreaId);
        this._monitorsId = this._bgAddedId = this._workAreaId = 0;
        this._settingIds = [];
        this._detachAll();
        this._settings = null;
        this._interface = null;
        this._drops = [];
        this._lens = null;
        this._orb = null;
        this._morph = null;
        this._agent = null;
    }

    // --- Modes ---------------------------------------------------------------------------------

    _isAgent() {
        return this._settings.get_string('driver') !== 'pointer';
    }

    _isLens() {
        return this._settings.get_string('style') !== 'water';
    }

    _coverAll() {
        return this._isAgent() || this._settings.get_boolean('cover-everything');
    }

    // --- The agent sphere ----------------------------------------------------------------------

    // Where the orb rests: where you last dropped it, or the bottom-left corner.
    _homePos() {
        if (this._island) return this._island.ringCenter(); // top-edge island: home is its ring
        const wa = Main.layoutManager.getWorkAreaForMonitor(Main.layoutManager.primaryIndex);
        const r = this._settings.get_double('orb-size') / 2;
        const [fx, fy] = this._settings.get_value('orb-home').deepUnpack();
        if (fx < 0 || fy < 0) return {x: wa.x + HOME_MARGIN + r, y: wa.y + wa.height - HOME_MARGIN - r};
        return this._clampToWorkArea(wa.x + fx * wa.width, wa.y + fy * wa.height);
    }

    _clampToWorkArea(x, y) {
        const wa = Main.layoutManager.getWorkAreaForMonitor(Main.layoutManager.primaryIndex);
        const r = this._settings.get_double('orb-size') / 2 + 4;
        return {x: Math.min(Math.max(x, wa.x + r), wa.x + wa.width - r),
            y: Math.min(Math.max(y, wa.y + r), wa.y + wa.height - r)};
    }

    _saveHome(x, y) {
        const wa = Main.layoutManager.getWorkAreaForMonitor(Main.layoutManager.primaryIndex);
        this._settings.set_value('orb-home', new GLib.Variant('(dd)', [(x - wa.x) / wa.width, (y - wa.y) / wa.height]));
    }

    // Press on the orb and drag it anywhere; let go and that spot is its new home. A press that
    // doesn't move is an ordinary click (opens the command menu).
    _startDrag() {
        const A = this._agent;
        if (!A || A.awake) return;
        if (this._homeTimer) { GLib.source_remove(this._homeTimer); this._homeTimer = 0; }
        const [px, py] = global.get_pointer();
        this._drag = {ox: A.x - px, oy: A.y - py, px, py, moved: false};
        this._dragged = false;
        if (this._dragTimer) GLib.source_remove(this._dragTimer);
        this._dragTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ACTIVE_MS, () => {
            const D = this._drag;
            const [x, y, mods] = global.get_pointer();
            if (!D || !this._agent) { this._dragTimer = 0; return GLib.SOURCE_REMOVE; }
            if (!D.moved && Math.hypot(x - D.px, y - D.py) > DRAG_THRESHOLD) {
                D.moved = true;
                this._sphereMenu?.close();
                this._kick(0.4, 0.9); // picked up
            }
            if (D.moved) {
                const p = this._clampToWorkArea(x + D.ox, y + D.oy);
                this._agent.x = p.x;
                this._agent.y = p.y;
                this._placeSphereButton(p);
                this._wake();
            }
            if (!(mods & Clutter.ModifierType.BUTTON1_MASK)) {
                this._dragTimer = 0;
                if (D.moved) {
                    this._dragged = true;
                    this._saveHome(this._agent.x, this._agent.y);
                    this._kick(0.5, -1.2); // set down with a squash
                }
                this._drag = null;
                return GLib.SOURCE_REMOVE;
            }
            return GLib.SOURCE_CONTINUE;
        });
    }

    _setupAgent() {
        this._teardownAgentUI();
        if (!this._isAgent()) { this._agent = null; return; }
        const home = this._homePos();
        this._agent = this._agent ?? {x: home.x, y: home.y, awake: false};
        if (!this._agent.awake) Object.assign(this._agent, home);
        this._lens = null;
        if (this._isIsland()) {
            this._island = new Island({
                commands: COMMANDS,
                onRun: (cmd, prompt) => this._runCommand(cmd, prompt),
                onStop: () => this._stopTask(),
                onApprove: (a, yes) => this._core?.approve(a, yes).catch((e) => this._voice?.say(e.message === 'stale' ? 'That approval changed. Listen to the new one.' : `Approval failed: ${e.message}.`, 'high')),
                onSettings: () => this.openPreferences(),
                onTalk: (down) => this._talk(down),
                onUnknown: () => this._voice?.say('I can\u2019t do that yet. Try other words.', 'high'),
                onQuestion: () => this._voice?.say('That sounds like a question. I can\u2019t answer questions yet, but I can do things: ask me to open an app or type something.', 'high'),
                font: this._settings.get_string('ui-font'),
                // the pill resizes with its status: follow its ring, but never mid-flight
                onMoved: () => { if (!this._agent?.awake && !this._homeTimer) this._agentGoHome(true); },
            });
            this._agentGoHome(true);
        } else {
            this._addSphereButton();
        }
        this._wake();
    }

    // What commands drive: the sphere's position.
    _agentBody() {
        return {
            pos: () => (this._agent ? {x: this._agent.x, y: this._agent.y} : {x: 0, y: 0}),
            moveTo: (x, y) => {
                if (!this._agent) return;
                this._agent.x = x;
                this._agent.y = y;
                this._wake();
            },
            setAwake: (on) => {
                if (!this._agent) return;
                if (this._homeTimer) { GLib.source_remove(this._homeTimer); this._homeTimer = 0; }
                if (on && this._island) Object.assign(this._agent, this._homePos()); // leave from the island's ring
                this._agent.awake = on;
                if (on) this._kick(0.8, 1.6); // a little pop: "on it"
                this._wake();
            },
            flyHome: () => this._agentGoHome(false),
            caret: () => (this._caret ??= new CaretTracker()).position(),
            setTyping: (on) => { this._typing = on; this._wake(); },
            // ms a command waits before moving, so the ring can collapse into its droplet first
            startDelay: () => (this._isRing() ? MORPH_SECONDS * 1000 + 80 : 0),
        };
    }

    // Fly back to the corner (not cancellable by commands), then rest.
    _agentGoHome(snap) {
        if (!this._agent) return;
        const home = this._homePos();
        this._placeSphereButton(home);
        if (this._runner?.running) return; // a command is still using the sphere
        if (snap) { Object.assign(this._agent, home); this._wake(); return; }
        if (this._homeTimer) GLib.source_remove(this._homeTimer);
        const from = {x: this._agent.x, y: this._agent.y};
        const dist = Math.hypot(home.x - from.x, home.y - from.y);
        const ms = 1.6 * Math.min(1100, 350 + dist * 0.6); // as slow as the flow out (presets.js FLOW_SLOWDOWN)
        const t0 = GLib.get_monotonic_time();
        this._homeTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ACTIVE_MS, () => {
            const t = Math.min((GLib.get_monotonic_time() - t0) / 1000 / ms, 1);
            const e = easeInOut(t);
            // A gentle arc home.
            const bend = 0.18 * Math.sin(Math.PI * t) * dist;
            const nx = -(home.y - from.y) / (dist || 1), ny = (home.x - from.x) / (dist || 1);
            this._agent.x = mix(from.x, home.x, e) + nx * bend;
            this._agent.y = mix(from.y, home.y, e) + ny * bend;
            this._wake();
            if (t >= 1) {
                this._homeTimer = 0;
                this._agent.awake = false;
                if (this._island) Object.assign(this._agent, this._homePos()); // the ring may have moved meanwhile
                this._kick(0.5, -1.2); // lands with a squash and a wobble
                return GLib.SOURCE_REMOVE;
            }
            return GLib.SOURCE_CONTINUE;
        });
    }

    // An invisible, round click target over the resting sphere: click it for the command menu.
    _addSphereButton() {
        const size = this._settings.get_double('orb-size');
        this._sphereButton = new St.Button({
            reactive: true, can_focus: true, track_hover: true,
            width: size, height: size,
            style: `border-radius: ${size / 2}px;`,
            accessible_name: 'Artemis commands',
        });
        Main.layoutManager.addChrome(this._sphereButton, {trackFullscreen: true});
        this._sphereButton.connect('destroy', () => { this._sphereButton = null; });
        this._sphereMenu = new PopupMenu.PopupMenu(this._sphereButton, 0.5, St.Side.BOTTOM);
        Main.uiGroup.add_child(this._sphereMenu.actor);
        this._sphereMenu.actor.hide();
        this._fillMenu(this._sphereMenu);
        this._sphereMenuManager = new PopupMenu.PopupMenuManager(this._sphereButton);
        this._sphereMenuManager.addMenu(this._sphereMenu);
        this._sphereButton.connect('button-press-event', (_actor, event) => {
            if (event.get_button() === Clutter.BUTTON_PRIMARY) this._startDrag();
            return Clutter.EVENT_PROPAGATE;
        });
        this._sphereButton.connect('clicked', () => {
            if (this._drag?.moved || this._dragged) { this._dragged = false; return; } // that was a drag
            this._clickPulse();
            this._sphereMenu.toggle();
        });
        this._sphereButton.connect('notify::hover', () => { if (this._sphereButton.hover) this._kick(0.45, 0.8); });
        this._placeSphereButton(this._homePos());
    }

    _placeSphereButton(home) {
        if (!this._sphereButton) return;
        const size = this._sphereButton.width;
        this._sphereButton.set_position(Math.round(home.x - size / 2), Math.round(home.y - size / 2));
    }

    _teardownAgentUI() {
        this._island?.destroy();
        this._island = null;
        this._sphereMenu?.destroy();
        this._sphereMenu = null;
        this._sphereMenuManager = null;
        const button = this._sphereButton;
        if (button) {
            Main.layoutManager.removeChrome(button);
            button.destroy();
        }
        this._sphereButton = null;
    }

    // --- Commands menu (top bar and sphere) ----------------------------------------------------

    _addPanelButton() {
        this._stopItems = [];
        this._button = new PanelMenu.Button(0.0, 'Artemis');
        this._button.add_child(new St.Icon({icon_name: 'weather-showers-symbolic', style_class: 'system-status-icon'}));
        this._fillMenu(this._button.menu);
        Main.panel.addToStatusArea(this.uuid, this._button);
    }

    _fillMenu(menu) {
        menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem('Artemis commands'));
        for (const cmd of COMMANDS) {
            menu.addAction(cmd.name, () => this._runCommand(cmd));
        }
        menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const stop = menu.addAction('Stop', () => this._stopTask());
        stop.visible = this._runner?.running ?? false;
        this._stopItems.push(stop);
        stop.connect('destroy', () => { this._stopItems = this._stopItems?.filter((i) => i !== stop) ?? []; });
        this._wakeItem = new PopupMenu.PopupSwitchMenuItem('Listen for \u201cHey Artemis\u201d', this._settings.get_boolean('wake-word'));
        this._wakeItem.connect('toggled', (_i, on) => this._settings.set_boolean('wake-word', on));
        menu.addMenuItem(this._wakeItem);
        // Voice: every free ElevenLabs voice, one tap to switch (Artemis says a preview line in it)
        const voiceMenu = new PopupMenu.PopupSubMenuMenuItem('Voice');
        this._voiceItems = VOICES.map((v) => {
            const item = voiceMenu.menu.addAction(`${v.name} \u2014 ${v.about}`, () => this._settings.set_string('voice', v.key));
            item.setOrnament(v.key === this._settings.get_string('voice') ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
            return [v.key, item];
        });
        menu.addMenuItem(voiceMenu);
        menu.addAction('Put the orb back in the corner', () => this._settings.reset('orb-home'));
        menu.addAction('Settings…', () => this.openPreferences());
    }

    /** A short chime, so it is heard (not only seen) when Artemis starts and stops listening. Local mode only:
     *  main's core plays its own earcons. */
    _earcon(name) {
        try {
            const p = Gst.ElementFactory.make('playbin', null);
            p.set_property('uri', GLib.filename_to_uri(`${this.path}/${name}.wav`, null));
            p.set_property('volume', 0.6);
            const bus = p.get_bus();
            bus.add_signal_watch();
            bus.connect('message', (_b, m) => {
                if (m.type === Gst.MessageType.EOS || m.type === Gst.MessageType.ERROR) { bus.remove_signal_watch(); p.set_state(Gst.State.NULL); }
            });
            p.set_state(Gst.State.PLAYING);
        } catch (e) { console.warn(`Artemis earcon: ${e.message}`); }
    }

    // ----- main's core ---------------------------------------------------------------------------

    _coreConnected(on) {
        this._wakeOn?.();
        if (on) this._core.ready('Control+Shift+Space').catch(() => {}); // the core speaks its one ready line
        console.log(`Artemis island: ${on ? 'connected to' : 'disconnected from'} the core`);
    }

    /** One event from the core's stream (contracts §2) onto the island's panel. */
    _coreEvent(ev) {
        const I = this._island;
        if (!I) return;
        const d = ev.data ?? {}, tid = ev.task_id;
        const say = ev.announce?.text ?? '';
        switch (ev.type) {
        case 'voice.activity':
            // the core's real voice state (contracts §2): glow while it listens (push to talk or after a bare
            // "Hey Artemis"), "Transcribing…" while Whisper works, the bars move while Artemis speaks
            if (d.state === 'listening') I.setListening(true);
            else if (d.state === 'transcribing') I.setListening(false, true);
            else if (I._listening || I._hearing) I.setListening(false);
            I.setSpeaking(d.state === 'speaking');
            // push to talk is over only once the mic actually closes after listening (pressing also stops
            // Artemis speaking, so an 'idle' can arrive before 'listening' while the key is still held)
            if (this._coreVoice === 'listening' && d.state !== 'listening') this._coreTalking = false;
            this._coreVoice = d.state;
            break;
        case 'transcript.final':
            I.setListening(false);
            if (d.text) this._heard = d.text;
            break;
        case 'task.state': {
            let t = tid ? I.taskById(tid) : null;
            if (!t && ['queued', 'working'].includes(d.state)) {
                I.taskStart(this._heard ?? say.replace(/^Working on:\s*/, ''), null, tid);
                this._heard = null;
                t = I.taskById(tid);
            }
            if (!t) break;
            if (d.state === 'running' && say) I.taskStep(say.replace(/\.$/, ''));
            else if (d.state === 'succeeded') I.taskEnd(t.answer ?? 'Done.', false);
            else if (d.state === 'failed') I.taskEnd(t.error ?? 'The task failed.', true);
            else if (d.state === 'canceled') I.taskEnd('Canceled.', true);
            break;
        }
        case 'agent.message': {
            const t = tid ? I.taskById(tid) : null;
            if (!t) break;
            if (d.kind === 'progress' && !/^still working/i.test(d.text)) I.taskStep(d.text.replace(/\.$/, ''));
            else if (d.kind === 'progress') I.taskProgress('still working\u2026');
            else t.answer = d.text; // answer or question: the summary when the task ends
            break;
        }
        case 'file.changed': if (say) I.taskStep(say.replace(/\.$/, '')); break;
        case 'approval.requested': I.taskStep(d.kind === 'run_program' ? 'Waiting for your approval to run' : 'Waiting for your approval to write'); I.taskApproval(tid, d); break;
        case 'approval.resolved': I.taskApprovalResolved(d.approval_id); break;
        case 'run.result': I.taskStep(d.timed_out ? 'The run timed out' : `Ran it: exit code ${d.exit_code}`); break;
        case 'error': { const t = tid ? I.taskById(tid) : null; if (t) t.error = `${d.message} ${d.recovery ?? ''}`.trim(); break; }
        }
    }

    _stopTask() {
        const t = this._island?._tasks?.find((x) => x.state === 'working');
        if (this._core?.connected && t?.id) this._core.cancel(t.id).catch(() => {});
        else this._runner?.stop();
    }

    /** Push to talk: down starts listening (and stops Artemis speaking, as in main); up sends. */
    async _talk(down) {
        if (!this._listener || !this._island) return;
        if (this._core?.connected) {
            // main's core records and transcribes (faster-whisper); the transcript comes back as an event
            if (down === !!this._coreTalking) return;
            this._coreTalking = down;
            this._island.setListening(down, !down);
            this._core.talk(down).catch((e) => { this._coreTalking = false; this._island?.setListening(false); this._voice?.say(`Push to talk failed: ${e.message}.`, 'high'); });
            return;
        }
        if (this._wakeWord?.running) {
            // local Whisper: words stream into the panel and listening ends by itself when you pause
            if (down) {
                if (this._localTalking) return;
                this._localTalking = true;
                this._voice?.stop();
                this._island.setListening(true);
                this._wakeWord.listen();
            } else if (this._localTalking) {
                this._localTalking = false;
                this._island.setListening(false, true);
                this._wakeWord.stopListening();
            }
            return;
        }
        if (down) {
            if (this._listener.listening) return;
            this._voice?.stop();
            this._wakeWord?.setMuted(true); // push to talk owns the microphone
            try {
                this._listener.start();
                this._island.setListening(true);
            } catch (e) {
                this._island.setListening(false);
                this._voice?.say('The microphone is not available.', 'high');
            }
            return;
        }
        if (!this._listener.listening) return;
        this._island.setListening(false, true);
        this._wakeWord?.setMuted(false);
        try {
            const text = await this._listener.stop();
            this._island?.hear(text);
            if (!text) this._voice?.say('I didn\u2019t hear anything. Hold to talk, then release.', 'normal');
        } catch (e) {
            this._island?.hear('');
            this._voice?.say(`I couldn\u2019t hear that: ${e.message}.`, 'high');
        }
    }

    /** Run one of the agent's commands (from any menu, or a typed request on the island). */
    _runCommand(cmd, prompt = cmd.name) {
        this._island?.taskStart(prompt, cmd);
        if (this._runner) this._runner.onStep = (label) => this._island?.taskStep(label);
        if (this._runner) this._runner.onProgress = (detail) => this._island?.taskProgress(detail);
        this._stopItems.forEach((i) => { i.visible = true; });
        this._island?.setState('working', cmd.name.split(':')[0]);
        this._voice?.say(`Working on: ${cmd.name.split(':')[0]}.`, 'normal');
        this._runner.run(cmd.name, (r) => cmd.run(r), () => {
            if (this._runner?.running) return; // another command took over
            this._stopItems?.forEach((i) => { i.visible = false; });
            const err = this._runner.lastError;
            const said = err ? `Stopped: ${err}` : (cmd.summary ?? `Finished: ${cmd.name}`);
            this._island?.setState('done', '', said);
            this._island?.taskEnd(said, !!err);
            this._voice?.say(said, 'high'); // results interrupt, as in the core
            if (!err) this._doneGlow = 1; // the ball lights up green as it comes home
            this._wake();
        });
    }

    /** Drop one ring into the water at (x, y). */
    ripple(x, y, strength = 1) {
        if (!this._settings) return;
        if (!this._isAgent() && this._isLens()) return;
        if (this._isAgent()) {
            this._kick(0.18 * strength, 0.35 * strength);
            const r = this._settings.get_double('orb-size') / 2;
            this._addDrop(x, y, GLib.get_monotonic_time() / 1e6, CLICK_PUSH * strength * TRAIL_DEPTH, [70, 4, 0.6, r * 1.8]);
            this._wake();
            return;
        }
        const ringWidth = this._settings.get_double('wake-width');
        this._addDrop(x, y, GLib.get_monotonic_time() / 1e6, CLICK_PUSH * strength * ringWidth, this._form(0.6, this._settings));
        this._wake();
    }

    // --- Effects -------------------------------------------------------------------------------

    _targets() {
        if (this._coverAll()) {
            return [{actor: Main.layoutManager.uiGroup,
                rect: {x: 0, y: 0, width: global.stage.width, height: global.stage.height}}];
        }
        const out = [];
        (Main.layoutManager._bgManagers ?? []).forEach((m, i) => {
            const monitor = Main.layoutManager.monitors[m._monitorIndex ?? i];
            if (m.backgroundActor && monitor) out.push({actor: m.backgroundActor, rect: monitor});
        });
        return out;
    }

    _attachAll() {
        const wantWater = this._isAgent() || !this._isLens();
        const wantLens = this._isAgent() || this._isLens();
        for (const {actor} of this._targets()) {
            if (this._effects.has(actor)) continue;
            const entry = {water: null, lens: null, id: 0};
            if (wantWater) {
                entry.water = new WaterEffect();
                entry.water.enabled = false;
                actor.add_effect_with_name('artemis-water', entry.water);
            }
            if (wantLens) { // added after the water, so the sphere is drawn on top of its wake
                entry.lens = !this._isAgent() ? new LensEffect()
                    : this._settings.get_string('agent-look') === 'orb' ? new OrbEffect() : new RingEffect();
                entry.lens.enabled = false;
                actor.add_effect_with_name('artemis-lens', entry.lens);
            }
            entry.id = actor.connect('destroy', () => this._effects.delete(actor));
            this._effects.set(actor, entry);
        }
        this._waterOn = this._lensOn = false;
    }

    _detachAll() {
        for (const [actor, {water, lens, id}] of this._effects) {
            actor.disconnect(id);
            if (water) actor.remove_effect(water);
            if (lens) actor.remove_effect(lens);
        }
        this._effects.clear();
        this._waterOn = this._lensOn = false;
    }

    _reattachSoon() {
        if (this._reattachId) return;
        this._reattachId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 250, () => {
            this._reattachId = 0;
            this._detachAll();
            this._attachAll();
            this._wake();
            return GLib.SOURCE_REMOVE;
        });
    }

    _setWater(on) {
        if (this._waterOn === on) return;
        this._waterOn = on;
        for (const {water} of this._effects.values()) if (water) water.enabled = on;
    }

    _setLens(on) {
        if (this._lensOn === on) return;
        this._lensOn = on;
        for (const {lens} of this._effects.values()) if (lens) lens.enabled = on;
    }

    // --- Loop ----------------------------------------------------------------------------------

    _schedule(ms) {
        if (this._timer) GLib.source_remove(this._timer);
        this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            this._timer = 0;
            this._tick();
            return GLib.SOURCE_REMOVE;
        });
    }

    // Run the next frame right away (something started moving).
    _wake() {
        if (this._settings && (!this._timer || this._sleeping)) {
            this._sleeping = false;
            this._schedule(ACTIVE_MS);
        }
    }

    _tick() {
        const now = GLib.get_monotonic_time() / 1e6;
        const s = this._settings;
        if (this._isAgent()) { this._tickAgent(now, s); return; }

        const life = s.get_double('ripple-life');
        const allowed = this._interface.enable_animations && !Main.overview.visible;
        const [x, y, mods] = global.get_pointer();
        const pressed = (mods & Clutter.ModifierType.BUTTON1_MASK) !== 0;
        const stirs = allowed && (this._coverAll() || this._isOverDesktop(x, y));

        if (this._isLens()) {
            const busy = this._tickLens(now, x, y, stirs ? 'follow' : 'hide', s);
            this._last = {x, y, t: now};
            this._schedule(busy ? ACTIVE_MS : IDLE_MS);
            return;
        }
        if (stirs) this._sample(x, y, now, pressed, s);
        if (!allowed) this._drops = [];
        this._buttonDown = pressed;
        this._last = {x, y, t: now};
        const moving = this._tickWater(now, s, life);
        this._schedule(moving ? ACTIVE_MS : IDLE_MS);
    }

    _tickAgent(now, s) {
        const A = this._agent;
        if (!A) { this._schedule(AGENT_IDLE_MS); return; }
        const monitor = Main.layoutManager.primaryMonitor;
        const hidden = Main.overview.visible || (!A.awake && monitor?.inFullscreen);
        const animate = this._interface.enable_animations;

        // The sphere's own motion stirs the water (not the mouse).
        if (!hidden && animate && !this._typing) this._trailSample(A.x, A.y, now, s);
        else this._drops = [];
        this._last = {x: A.x, y: A.y, t: now};
        const water = this._tickWater(now, s, TRAIL_LIFE);
        // Ring look: the whole ring collapses into a droplet that flies the command (leaving the
        // trail) and opens back into the ring at home.
        const morph = this._isRing() ? this._tickMorph(now, A) : {value: 0, busy: false};
        // Top-edge island: at home the droplet hides behind the pill's ring; it only shows in flight.
        const tucked = false; // island: the ring rests, small, in the pill's slot
        const orb = this._tickOrb(now, A.x, A.y, hidden || tucked || this._typing, s, morph);
        if (this._sphereButton) this._sphereButton.visible = !hidden && !A.awake && morph.value === 0;

        if (water || orb === 'busy' || A.awake) { this._sleeping = false; this._schedule(ACTIVE_MS); }
        else if (orb === 'alive') { this._sleeping = true; this._schedule(ORB_LIVE_MS); } // idling, gently
        else { this._sleeping = true; this._schedule(AGENT_IDLE_MS); } // frozen: one static frame
    }

    // --- Agent orb -----------------------------------------------------------------------------

    // Ring look: how far the whole ring has collapsed into one droplet (0 = ring, 1 = droplet).
    // It collapses when a command starts, travels as the droplet, and opens back into the ring once
    // it is home again. Returns the eased value; .busy while it is changing.
    _tickMorph(now, A) {
        const M = (this._morph ??= {m: 0, t: now});
        const dt = Math.min(Math.max(now - M.t, 0), 0.05);
        M.t = now;
        const home = this._homePos();
        const away = A.awake || (!this._drag && Math.hypot(A.x - home.x, A.y - home.y) > 2);
        const before = M.m;
        M.m = Math.min(Math.max(M.m + (away ? 1 : -1) * dt / MORPH_SECONDS, 0), 1);
        return {value: smooth(M.m), busy: M.m !== before || (M.m > 0 && M.m < 1)};
    }

    _isIsland() {
        return this._isAgent() && this._settings.get_string('agent-look') === 'island';
    }

    _isRing() {
        return this._isAgent() && this._settings.get_string('agent-look') !== 'orb';
    }

    /** Clicked: a little pop, a flare of the halo and a small ripple. */
    _clickPulse() {
        const O = this._orb;
        if (!O) return;
        O.flash = CLICK_FLASH;
        O.seed = Math.random() * 50;
        this._kick(0.3, 0.7);
        this.ripple(O.x, O.y, 0.4);
    }

    /** Wobble the orb (energy 0..1) and give its size a kick (scale velocity, 1/s). */
    _kick(energy, pop = 0) {
        const O = this._orb;
        if (!O) return;
        O.energy = Math.min(O.energy + energy, 1.2);
        O.sv += pop;
        this._wake();
    }

    /** Returns 'busy' (moving or settling), 'alive' (idling with its slow motion) or false. */
    _tickOrb(now, x, y, hidden, s, morph = {value: 0, busy: false}) {
        if (!this._orb) {
            this._orb = {x, y, vx: 0, vy: 0, vis: 0, dir: [1, 0], stretch: 0, t: now,
                morph: Math.random() * 100, spin: Math.random() * 6.28, energy: 0.7, scale: 0.7, sv: 0, flash: 0};
        }
        const O = this._orb;
        const dt = Math.min(Math.max(now - O.t, 0), 0.05);
        O.t = now;
        if (O.vis <= 0) { O.x = x; O.y = y; O.vx = O.vy = 0; O.stretch = 0; }

        // The orb sits exactly where it is going (no lag); its velocity only shapes it.
        const jump = Math.hypot(x - O.x, y - O.y);
        if (dt > 0) {
            const ivx = jump > MAX_JUMP ? 0 : (x - O.x) / dt, ivy = jump > MAX_JUMP ? 0 : (y - O.y) / dt;
            const a = Math.min(1, dt * 20);
            O.vx += (ivx - O.vx) * a;
            O.vy += (ivy - O.vy) * a;
            if (Math.hypot(O.vx, O.vy) < 1 && jump === 0) O.vx = O.vy = 0;
        }
        O.x = x;
        O.y = y;
        for (let t = 0; t < dt - 1e-9; t += 1 / 240) {
            const h = Math.min(1 / 240, dt - t);
            O.sv += (ORB_POP_K * (1 - O.scale) - ORB_POP_D * O.sv) * h;
            O.scale += O.sv * h;
        }
        const speed = Math.hypot(O.vx, O.vy);
        if (speed > 20) O.dir = [O.vx / speed, O.vy / speed];
        const stretchTo = Math.min(speed / LENS_STRETCH_SPEED, 1) * LENS_MAX_STRETCH;
        O.stretch += (stretchTo - O.stretch) * Math.min(1, dt * 12);
        // Moving fast churns it; otherwise any wobble calms down.
        O.energy = Math.max(O.energy * Math.exp(-dt * ORB_ENERGY_DECAY), Math.min(speed / 2500, 0.45));
        O.flash *= Math.exp(-dt * RING_FLASH_DECAY);
        this._doneGlow = (this._doneGlow ?? 0) * Math.exp(-dt * 0.7);
        if (this._doneGlow < 0.01) this._doneGlow = 0;
        if (O.flash < 0.01) O.flash = 0;
        // Held: while its menu is open the ring stays lively: a steady jiggle, more glow, and
        // little random pokes to its jelly spring so the physics keeps going.
        const holding = !!this._sphereMenu?.isOpen;
        O.hold = (O.hold ?? 0) + ((holding ? 1 : 0) - (O.hold ?? 0)) * Math.min(1, dt * HOLD_EASE);
        if (!holding && O.hold < 0.01) O.hold = 0;
        if (holding && now >= (O.nextPoke ?? 0)) {
            O.nextPoke = now + (HOLD_POKE_MS[0] + Math.random() * (HOLD_POKE_MS[1] - HOLD_POKE_MS[0])) / 1000;
            O.energy = Math.min(O.energy + 0.12, 1.2);
            O.sv += (Math.random() < 0.5 ? -1 : 1) * (0.35 + Math.random() * 0.35);
        }

        const want = hidden ? 0 : 1;
        O.vis += (want - O.vis) * (1 - Math.exp(-dt / LENS_FADE));
        if (want === 0 && O.vis < 0.01) {
            O.vis = 0;
            this._setLens(false);
            return false;
        }
        if (want === 1 && O.vis > 0.995) O.vis = 1;

        const live = s.get_boolean('orb-motion') && this._interface.enable_animations;
        const settled = speed < 1 && Math.hypot(x - O.x, y - O.y) < 0.5 && O.stretch < 0.002 &&
            Math.abs(O.scale - 1) < 0.002 && Math.abs(O.sv) < 0.01 && O.energy < 0.02 && O.flash === 0 && !this._doneGlow && !O.hold && O.vis === 1 &&
            !morph.busy;
        if (live || !settled) {
            // The slow life of the orb: its outline morphs and it tumbles, both faster when stirred.
            O.morph += dt * (1 + O.energy * 1.6);
            // spins up while a command runs (the ring's "thinking swirl")
            O.spin += dt * (0.4 + O.energy * 1.4 + speed / 900) * (this._agent?.awake ? 3 : 1);
        }
        if (live || !settled || !this._lensOn) {
            const radius = s.get_double('orb-size') / 2 * Math.max(O.scale, 0.3) * (this._isIsland() && !this._agent?.awake ? 0.42 : 1);
            const shape = [O.dir[0], O.dir[1], O.stretch, live ? O.energy : O.energy * (settled ? 0 : 1)];
            const light = [s.get_double('highlights'), ORB_DISPERSION, O.morph % 1000, O.spin % 6283.1853];
            const working = this._agent?.awake ? 0.45 : 0; // shines while it works
            const glow = RING_GLOW + working + RING_FLASH_GLOW * O.flash + HOLD_GLOW * O.hold + 0.1 * Math.min(O.energy, 1);
            const wobble = Math.max(O.flash, HOLD_WOBBLE * O.hold);
            for (const {actor, rect} of this._targets()) {
                const orb = this._effects.get(actor)?.lens;
                if (!orb) continue;
                orb.update([rect.width, rect.height], [O.x - rect.x, O.y - rect.y, radius, O.vis], shape, light, glow, morph.value, wobble, O.seed ?? 0, O.flash,
                    this._agent?.awake ? [0.85, 0.6, 1.0, 0.3] : [1, 1, 1, 0]);
            }
        }
        this._setLens(true);
        return settled ? (live ? 'alive' : false) : 'busy';
    }

    // --- Lens / sphere -------------------------------------------------------------------------

    /**
     * mode: 'follow' (pointer driver: visible while moving), 'show' (agent: always visible),
     * 'hide'. Returns true while it is still animating.
     */
    _tickLens(now, x, y, mode, s) {
        const last = this._last;
        const jumped = last && Math.hypot(x - last.x, y - last.y) > MAX_JUMP;
        const moved = last && !jumped && Math.hypot(x - last.x, y - last.y) > 0.5;
        if (!this._lens)
            this._lens = {x, y, vx: 0, vy: 0, vis: 0, dir: [1, 0], stretch: 0, lastMove: -1e9, t: now, time: 0};
        const L = this._lens;
        if (L.vis <= 0 || (jumped && mode !== 'show')) {
            L.x = x; L.y = y; L.vx = L.vy = 0; L.stretch = 0;
        }
        const dt = Math.min(Math.max(now - L.t, 0), 0.05);
        L.t = now;
        if (moved && mode === 'follow') L.lastMove = now;

        for (let t = 0; t < dt - 1e-9; t += 1 / 240) {
            const h = Math.min(1 / 240, dt - t);
            L.vx += (LENS_STIFFNESS * (x - L.x) - LENS_DAMPING * L.vx) * h;
            L.vy += (LENS_STIFFNESS * (y - L.y) - LENS_DAMPING * L.vy) * h;
            L.x += L.vx * h;
            L.y += L.vy * h;
        }
        const speed = Math.hypot(L.vx, L.vy);
        if (speed > 20) L.dir = [L.vx / speed, L.vy / speed];
        const stretchTo = Math.min(speed / LENS_STRETCH_SPEED, 1) * LENS_MAX_STRETCH;
        L.stretch += (stretchTo - L.stretch) * Math.min(1, dt * 12);

        const want = mode === 'show' ? 1 : mode === 'follow' && now - L.lastMove < LENS_SHOW_FOR ? 1 : 0;
        L.vis += (want - L.vis) * (1 - Math.exp(-dt / LENS_FADE));
        if (want === 0 && L.vis < 0.01) {
            L.vis = 0;
            this._setLens(false);
            return false;
        }
        if (want === 1 && L.vis > 0.995) L.vis = 1;

        const settled = speed < 1 && Math.hypot(x - L.x, y - L.y) < 0.5 && L.stretch < 0.002 && (L.vis === 1 || L.vis === 0);
        // The outline only "breathes" while it is moving; at rest it holds still (no repaints).
        if (!settled) L.time += dt;
        const radius = s.get_double('lens-size') / 2;
        const shape = [L.dir[0], L.dir[1], L.stretch, s.get_double('lens-zoom')];
        const light = [s.get_double('highlights'), LENS_DISPERSION, L.time % 1000];
        if (!settled || !this._lensOn) {
            for (const {actor, rect} of this._targets()) {
                const lens = this._effects.get(actor)?.lens;
                if (lens) lens.update([rect.width, rect.height], [L.x - rect.x, L.y - rect.y, radius, L.vis], shape, light);
            }
        }
        this._setLens(true);
        return !settled;
    }

    // --- Water ---------------------------------------------------------------------------------

    // Ages the drops and renders them; returns true while any water is moving.
    _tickWater(now, s, life) {
        this._drops = this._drops.filter((d) => now - d.t < life);
        if (this._drops.length === 0) {
            this._setWater(false);
            return false;
        }
        this._render(now, s, life);
        return true;
    }

    _sample(x, y, now, pressed, s) {
        const ringWidth = s.get_double('wake-width');
        if (pressed && !this._buttonDown) this._addDrop(x, y, now, CLICK_PUSH * ringWidth, this._form(0.6, s));

        const last = this._last;
        if (!last) return;
        const dt = Math.max(now - last.t, 1e-3);
        const moved = Math.hypot(x - last.x, y - last.y);
        if (moved > MAX_JUMP) return;
        const speed = moved / dt;
        if (speed < MIN_SPEED) return;
        const f = smooth((speed - SLOW_SPEED) / (FAST_SPEED - SLOW_SPEED));
        const spacing = mix(FORM_SLOW.spacing, FORM_FAST.spacing, f);
        const prev = this._lastDrop;
        if (prev && Math.hypot(x - prev.x, y - prev.y) < spacing) return;
        if (prev && (now - prev.t) * 1000 < DROP_MIN_MS) return;
        const push = PUSH * (PUSH_FLOOR + (1 - PUSH_FLOOR) * (1 - Math.exp(-speed / SPEED_REF)));
        this._addDrop(x, y, now, push * ringWidth, this._form(f, s));
    }

    // Agent orb: a short, thin trail right behind it. Each point is a narrow groove that barely
    // spreads and is gone in TRAIL_LIFE, so only the last bit of the path shows.
    _trailSample(x, y, now, s) {
        const last = this._last;
        if (!last) return;
        const dt = Math.max(now - last.t, 1e-3);
        const moved = Math.hypot(x - last.x, y - last.y);
        if (moved > MAX_JUMP) return;
        const speed = moved / dt;
        if (speed < 40) return;
        const prev = this._lastDrop;
        if (prev && Math.hypot(x - prev.x, y - prev.y) < TRAIL_SPACING) return;
        if (prev && (now - prev.t) * 1000 < TRAIL_MIN_MS) return;
        const r = s.get_double('orb-size') / 2;
        const k = Math.min(speed / 1500, 1);
        this._addDrop(x, y, now, (0.25 + 0.35 * k) * TRAIL_DEPTH, [25, 4 + 3 * k, 1.4, r * (0.8 + 0.5 * k)]);
    }

    // Per-drop form [ringSpeed, ringWidth, wakeGain, reach] for a slow-to-fast blend f.
    _form(f, s) {
        const w = s.get_double('wake-width'), R = s.get_double('active-radius');
        return [mix(FORM_SLOW.ringSpeed, FORM_FAST.ringSpeed, f), w * mix(FORM_SLOW.ringWidth, FORM_FAST.ringWidth, f),
            mix(FORM_SLOW.wake, FORM_FAST.wake, f), R * mix(FORM_SLOW.reach, FORM_FAST.reach, f)];
    }

    _addDrop(x, y, t, amp, form) {
        this._drops.push({x, y, t, amp, form});
        if (this._drops.length > MAX_DROPS) this._drops.shift();
        this._lastDrop = {x, y, t};
    }

    _render(now, s, life) {
        const ringWidth = s.get_double('wake-width');
        const wave = [RING_SPEED, ringWidth, ringWidth * 0.8, life];
        const distortion = s.get_double('distortion');
        const agent = this._isAgent();
        const look = [BEND_GAIN * distortion, s.get_double('active-radius'),
            s.get_double('highlights') * (agent ? TRAIL_HIGHLIGHTS : 1), DISPERSION * (agent ? TRAIL_DISPERSION : 1)];
        const cap = BEND_CAP * distortion;
        const trail = agent ? [...TRAIL_TINT, TRAIL_GLOW] : [1, 1, 1, 0];
        for (const {actor, rect} of this._targets()) {
            const water = this._effects.get(actor)?.water;
            if (!water) continue;
            const ox = rect.x, oy = rect.y;
            const drops = this._drops.map((d) => ({x: d.x - ox, y: d.y - oy, age: now - d.t, amp: d.amp, form: d.form}));
            water.update(drops, [rect.width, rect.height, 0, 0], wave, look, cap, trail);
        }
        this._setWater(true);
    }

    // Wallpaper-only scope: the pointer is "on the desktop" when no ordinary window is under it.
    _isOverDesktop(x, y) {
        const actors = global.get_window_actors();
        for (let i = actors.length - 1; i >= 0; i--) {
            const a = actors[i];
            const w = a.meta_window;
            if (!w || !a.visible || w.minimized || w.get_window_type() === Meta.WindowType.DESKTOP) continue;
            if (!w.located_on_workspace(global.workspace_manager.get_active_workspace())) continue;
            const r = w.get_frame_rect();
            if (x >= r.x && x < r.x + r.width && y >= r.y && y < r.y + r.height) return false;
        }
        return true;
    }
}
