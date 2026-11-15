// What the island does for a desktop.action (docs/contracts.md §9): read the focused window, and type or
// press keys into it through a Clutter virtual keyboard. Only the GNOME shell can type on Wayland, so
// the core asks the island and waits for POST /desktop/{op_id}/result. Typed text is never logged.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export const CHAR_MS = 30;          // paced per character; the core allows 50 ms (desktop.py PER_CHAR_S)
const HOLD_MS = 10;                 // how long each key stays down
const MODIFIERS = {ctrl: 'Control_L', shift: 'Shift_L', alt: 'Alt_L'};

// "<app id>: <title>" for the focused window, or null when no window has the keyboard (the overview,
// a shell prompt). The app id is the .desktop id, e.g. "org.gnome.gedit.desktop".
export function focusedWindow() {
    const win = global.display.focus_window;
    if (!win)
        return null;
    const app = Shell.WindowTracker.get_default().get_window_app(win);
    const id = app?.get_id() ?? win.get_wm_class() ?? 'unknown';
    return `${id}: ${win.get_title() ?? ''}`;
}

const sleep = (ms) => new Promise((resolve) => {
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
        resolve();
        return GLib.SOURCE_REMOVE;
    });
});

// A key name ("Return", "grave", "s") to its keysym. Clutter exports one KEY_<name> constant per keysym;
// this build has no keyval_from_name, so the constants are the lookup.
const keyval = (name) => {
    const k = Clutter[`KEY_${name}`] ?? (name.length === 1 ? Clutter.unicode_to_keysym(name.codePointAt(0)) : undefined);
    if (!k)
        throw new Error(`unknown key ${name}`);
    return k;
};

// A virtual keyboard for this island. create() returns null when the shell cannot make one (then the
// island does not tell the core it can type). Only one job runs at a time.
export class Typist {
    static create() {
        try {
            const seat = Clutter.get_default_backend().get_default_seat();
            const device = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
            return device ? new Typist(device) : null;
        } catch (e) {
            return null;
        }
    }

    constructor(device) {
        this._device = device;
        this._held = new Set();
        this._stopped = false;
        this._generation = 0;
    }

    // Called when the task is canceled: the job in progress, and any queued one, stops at its next keystroke.
    stop() {
        this._stopped = true;
        this._generation++;
        this._releaseAll();
    }

    // Called when a new task starts, so a later job is not refused by an earlier cancel.
    resume() {
        this._stopped = false;
    }

    destroy() {
        this.stop();
        this._releaseAll();
        this._device = null;
    }

    get generation() {
        return this._generation;
    }

    // kind: 'type' (text, "\n" is Return) or 'keys' (a chord such as "ctrl+s"). window is the
    // "<app id>: <title>" the developer approved. Resolves to {ok, message}.
    async run(kind, text, window, generation = this._generation) {
        if (!this._device)
            return {ok: false, message: 'the keyboard is not available, so nothing was typed'};
        // Checked before every keystroke: the same window, and no shell prompt or dialog holding the keyboard.
        const guard = () => !this._stopped && generation === this._generation && Main.modalCount === 0 && !!window && focusedWindow() === window;
        if (!window)
            return {ok: false, message: 'no window was approved for typing, so nothing was typed'};
        if (!guard())
            return {ok: false, message: this._stopped ? 'the task was canceled, so nothing was typed' : 'the focus moved, so nothing was typed'};
        try {
            if (kind === 'keys')
                await this._chord(text);
            else
                return await this._type(text, guard);
            return {ok: true};
        } catch (e) {
            return {ok: false, message: e.message};
        } finally {
            this._releaseAll();
        }
    }

    async _type(text, guard) {
        const chars = Array.from(text);
        let typed = 0;
        for (const ch of chars) {
            if (!guard()) {
                if (this._stopped)
                    return {ok: false, message: `the task was canceled, so typing stopped after ${typed} of ${chars.length} characters`};
                const message = typed === 0
                    ? 'the focus moved, so nothing was typed'
                    : `the focus moved, so typing stopped after ${typed} of ${chars.length} characters`;
                return {ok: false, message};
            }
            if (ch === '\r')
                continue;
            const kv = ch === '\n' ? Clutter.KEY_Return : Clutter.unicode_to_keysym(ch.codePointAt(0));
            this._press(kv);
            await sleep(HOLD_MS);
            this._release(kv);
            await sleep(CHAR_MS - HOLD_MS);
            typed++;
        }
        return {ok: true};
    }

    async _chord(spec) {
        const parts = spec.split('+');
        const name = parts.pop();
        const mods = parts.map((m) => keyval(MODIFIERS[m]));
        const kv = keyval(name);
        mods.forEach((m) => this._press(m));
        this._press(kv);
        await sleep(HOLD_MS * 4);
        this._release(kv);
        mods.reverse().forEach((m) => this._release(m));
    }

    _press(kv) {
        this._device.notify_keyval(GLib.get_monotonic_time(), kv, Clutter.KeyState.PRESSED);
        this._held.add(kv);
    }

    _release(kv) {
        if (!this._held.has(kv)) return;
        this._device?.notify_keyval(GLib.get_monotonic_time(), kv, Clutter.KeyState.RELEASED);
        this._held.delete(kv);
    }

    // A key left down would lock the keyboard: every key this job pressed is released.
    _releaseAll() {
        if (!this._device)
            return;
        for (const kv of [...this._held])
            this._release(kv);
    }
}
