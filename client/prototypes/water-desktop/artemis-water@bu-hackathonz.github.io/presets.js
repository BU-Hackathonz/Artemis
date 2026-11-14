// Commands for the Artemis sphere: scripted tasks the sphere "zips through". The sphere moves on
// its own (it never takes over your mouse pointer); apps are opened directly and text is typed
// with a virtual keyboard, the same way GNOME's on-screen keyboard does it.
//
// Safety:
//   - commands only type into a window they opened themselves, and only while it has focus and no
//     GNOME prompt (keyring, polkit, ...) holds the keyboard; checked before every key
//   - commands never click inside app windows
//   - "Stop" (or starting another command) cancels immediately, releases any held keys, and the
//     sphere flies home

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const STEP_MS = 16;

class Cancelled extends Error {}

const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const now = () => GLib.get_monotonic_time();

/**
 * body: {pos(): {x, y}, moveTo(x, y), setAwake(bool), flyHome(), startDelay?(): ms} (the sphere)
 * ripple(x, y, strength): drops one ring in the water
 */
export class CommandRunner {
    constructor(body, ripple) {
        this._body = body;
        this._ripple = ripple;
        const seat = Clutter.get_default_backend().get_default_seat();
        this._keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._job = null;
        this._held = new Set();
    }

    destroy() {
        this.stop();
        this._keyboard = null;
    }

    get running() {
        return this._job !== null;
    }

    stop() {
        if (!this._job) return;
        this._job.cancelled = true;
        for (const id of this._job.timers) GLib.source_remove(id);
        this._job.timers.clear();
        this._job.wake?.();
        this._releaseAll();
        this._job = null;
    }

    async run(name, command, onDone) {
        this.stop();
        const job = {cancelled: false, timers: new Set(), wake: null};
        this._job = job;
        this._body.setAwake(true);
        try {
            const delay = this._body.startDelay?.() ?? 0;
            if (delay > 0) await this.sleep(delay);
            await command(this);
        } catch (e) {
            if (!(e instanceof Cancelled)) {
                logError(e, `Artemis command "${name}"`);
                Main.notify('Artemis', `"${name}" stopped: ${e.message}`);
            }
        } finally {
            this._releaseAll();
            if (this._job === job) this._job = null;
            if (!this._job) this._body.flyHome();
            onDone?.();
        }
    }

    // --- Building blocks -----------------------------------------------------------------------

    sleep(ms) {
        const job = this._job;
        if (!job || job.cancelled) return Promise.reject(new Cancelled());
        return new Promise((resolve, reject) => {
            const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
                job.timers.delete(id);
                job.wake = null;
                if (job.cancelled) reject(new Cancelled()); else resolve();
                return GLib.SOURCE_REMOVE;
            });
            job.timers.add(id);
            job.wake = () => reject(new Cancelled());
        });
    }

    pos() {
        return this._body.pos();
    }

    screen() {
        return {w: global.stage.width, h: global.stage.height};
    }

    /** Zip the sphere to (x, y) along a gentle curve, easing in and out. */
    async fly(x, y, ms = 700, bend = 0.18) {
        const a = this.pos();
        const dx = x - a.x, dy = y - a.y;
        const c = {x: a.x + dx / 2 - dy * bend, y: a.y + dy / 2 + dx * bend};
        const steps = Math.max(1, Math.round(ms / STEP_MS));
        for (let i = 1; i <= steps; i++) {
            const t = easeInOut(i / steps);
            const u = 1 - t;
            this._body.moveTo(u * u * a.x + 2 * u * t * c.x + t * t * x, u * u * a.y + 2 * u * t * c.y + t * t * y);
            await this.sleep(STEP_MS);
        }
    }

    /** A ring in the water at the sphere. */
    tap(strength = 1) {
        const p = this.pos();
        this._ripple(p.x, p.y, strength);
    }

    /** Keys go only where guard() allows (see safety notes at the top). */
    async key(keyval, modifiers = [], guard = null) {
        if (guard && !guard()) throw new Error('something else took the keyboard, so nothing was typed');
        for (const m of modifiers) this._press(m);
        this._press(keyval);
        await this.sleep(40);
        this._release(keyval);
        for (const m of [...modifiers].reverse()) this._release(m);
    }

    /** Type at a human pace; each keystroke sends a small ripple from the sphere. */
    async type(text, guard, perChar = 85) {
        for (const ch of text) {
            if (guard && !guard()) throw new Error('something else took the keyboard, so typing stopped');
            const keyval = ch === '\n' ? Clutter.KEY_Return : Clutter.unicode_to_keysym(ch.codePointAt(0));
            this._press(keyval);
            await this.sleep(35);
            this._release(keyval);
            this.tap(ch === ' ' ? 0.25 : 0.4);
            await this.sleep(perChar + Math.round((Math.random() - 0.5) * perChar * 0.6));
        }
    }

    /** Wait until fn() returns something truthy (polling), or fail after ms. */
    async waitFor(fn, ms, what) {
        const until = now() + ms * 1000;
        for (;;) {
            const v = fn();
            if (v) return v;
            if (now() > until) throw new Error(`timed out waiting for ${what}`);
            await this.sleep(100);
        }
    }

    _press(keyval) {
        this._keyboard.notify_keyval(now(), keyval, Clutter.KeyState.PRESSED);
        this._held.add(keyval);
    }

    _release(keyval) {
        this._keyboard.notify_keyval(now(), keyval, Clutter.KeyState.RELEASED);
        this._held.delete(keyval);
    }

    _releaseAll() {
        for (const k of [...this._held]) this._release(k);
    }
}

// --- Helpers ------------------------------------------------------------------------------------

/** Centre of an app's launcher icon in the dock/dash, if one is visible. */
function findAppIcon(appId) {
    const stack = [global.stage];
    while (stack.length) {
        const a = stack.pop();
        try {
            if (a.app?.get_id?.() === appId && a.mapped && a.width > 8) {
                const [x, y] = a.get_transformed_position();
                const [w, h] = a.get_transformed_size();
                return {x: x + w / 2, y: y + h / 2};
            }
        } catch { /* not every actor has these */ }
        for (const c of a.get_children?.() ?? []) stack.push(c);
    }
    return null;
}

const windowsMatching = (re) => global.display.list_all_windows()
    .filter((w) => re.test(w.get_wm_class() ?? '') || re.test(w.get_gtk_application_id?.() ?? ''));

/** Launch argv and wait for a new window whose class matches re. */
async function openWindow(r, argv, re, what, timeout = 25000) {
    const before = new Set(windowsMatching(re));
    try {
        Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE);
    } catch (e) {
        throw new Error(`couldn't start ${what} (${e.message})`);
    }
    return r.waitFor(() => windowsMatching(re).find((w) => !before.has(w) && w.get_frame_rect().width > 200),
        timeout, `the new ${what} window`);
}

/** Fly to the app's dock icon (or a point on the way) and ring the water there. */
async function visitIcon(r, appId) {
    const {w, h} = r.screen();
    const icon = findAppIcon(appId) ?? {x: w * 0.3, y: h * 0.6};
    await r.fly(icon.x, icon.y, 650, 0.22);
    r.tap(1);
    await r.sleep(180);
}

const centreOf = (win, fx = 0.5, fy = 0.5) => {
    const f = win.get_frame_rect();
    return {x: f.x + f.width * fx, y: f.y + f.height * fy};
};

/** Safe-to-type check for one window: it has focus, and no GNOME prompt holds the keyboard. */
const safeFor = (win) => () => Main.modalCount === 0 && global.display.focus_window === win;

async function typeInto(r, win, text, settleMs = 900) {
    const safe = safeFor(win);
    await r.waitFor(safe, 6000, 'the window to take focus (is a system prompt open?)');
    await r.sleep(settleMs);
    const p = centreOf(win, 0.3, 0.25);
    await r.fly(p.x, p.y, 450, 0.1);
    await r.type(text, safe);
}

// --- Commands -----------------------------------------------------------------------------------

export const COMMANDS = [
    {
        name: 'VS Code: type "Hello world"',
        async run(r) {
            const appId = 'com.microsoft.VSCode.desktop';
            if (!Shell.AppSystem.get_default().lookup_app(appId)) throw new Error('Visual Studio Code is not installed');
            await visitIcon(r, appId);
            const win = await openWindow(r, ['code', '--new-window'], /vscode|code/i, 'VS Code');
            const c = centreOf(win);
            await r.fly(c.x, c.y, 600, -0.2);
            r.tap(1);
            await r.waitFor(safeFor(win), 6000, 'VS Code to take focus (is a system prompt open?)');
            await r.sleep(1500); // let the editor finish loading
            await r.key(Clutter.KEY_n, [Clutter.KEY_Control_L], safeFor(win));
            await typeInto(r, win, 'Hello world', 700);
        },
    },
    {
        name: 'Terminal: run echo "Hello from Artemis"',
        async run(r) {
            const appId = 'org.gnome.Ptyxis.desktop';
            await visitIcon(r, appId);
            const win = await openWindow(r, ['ptyxis', '--new-window'], /ptyxis/i, 'Terminal');
            const c = centreOf(win);
            await r.fly(c.x, c.y, 550, -0.2);
            r.tap(1);
            await typeInto(r, win, 'echo "Hello from Artemis"\n', 800);
        },
    },
    {
        name: 'Text Editor: write a short note',
        async run(r) {
            const appId = 'org.gnome.TextEditor.desktop';
            await visitIcon(r, appId);
            const win = await openWindow(r, ['gnome-text-editor', '--new-window'], /texteditor/i, 'Text Editor');
            const c = centreOf(win);
            await r.fly(c.x, c.y, 550, -0.2);
            r.tap(1);
            await typeInto(r, win, 'Notes from Artemis\n- the liquid glass works\n- it zips through tasks\n', 700);
        },
    },
    {
        name: 'Files: open your Home folder',
        async run(r) {
            await visitIcon(r, 'org.gnome.Nautilus.desktop');
            const win = await openWindow(r, ['nautilus', '--new-window', GLib.get_home_dir()], /nautilus/i, 'Files');
            // A quick look around the window.
            for (const [fx, fy] of [[0.5, 0.5], [0.25, 0.3], [0.75, 0.3], [0.5, 0.7]]) {
                const p = centreOf(win, fx, fy);
                await r.fly(p.x, p.y, 380, 0.2);
                r.tap(0.6);
            }
        },
    },
    {
        name: 'Browser: open example.com',
        async run(r) {
            const {w, h} = r.screen();
            await r.fly(w * 0.5, h * 0.12, 650, 0.25);
            r.tap(1);
            Gio.AppInfo.launch_default_for_uri('https://example.com', null);
            await r.sleep(2200); // the page may open as a tab in an existing window
            await r.fly(w * 0.5, h * 0.45, 600, -0.2);
            r.tap(0.8);
        },
    },
    {
        name: 'Tour the screen',
        async run(r) {
            const {w, h} = r.screen();
            const stops = [[0.08, 0.12], [0.92, 0.12], [0.92, 0.85], [0.5, 0.5], [0.08, 0.85], [0.5, 0.25]];
            for (const [fx, fy] of stops) {
                await r.fly(w * fx, h * fy, 520, 0.25);
                r.tap(0.8);
                await r.sleep(120);
            }
        },
    },
    {
        name: 'Peek at Activities',
        async run(r) {
            await r.fly(40, 40, 600, 0.25);
            r.tap(1);
            Main.overview.show();
            await r.sleep(1800);
            Main.overview.hide();
            await r.sleep(500);
        },
    },
];
