// "Open VS Code": the droplet flies to the app's icon in the dock, presses it (the icon's own launch
// zoom plays), waits there while the app starts, then flies onto the new window, as if it opened the
// app from the dock. Read-only on the dock: nothing here launches anything (the core already did,
// after its approval). Every lookup is guarded, so a missing or different dock only skips the visit.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {appMatches} from './ringTargets.js';

const MAX_DEPTH = 16;

/** The Shell.App a cue's target names: a running app first, then any installed one. */
export function findApp(target) {
    try {
        const sys = Shell.AppSystem.get_default();
        for (const app of sys.get_running())
            if (appMatches(target, {id: app.get_id(), name: app.get_name()})) return app;
        for (const info of Gio.AppInfo.get_all()) {
            if (!info.should_show() || !appMatches(target, {id: info.get_id(), name: info.get_name()})) continue;
            const app = sys.lookup_app(info.get_id());
            if (app) return app;
        }
    } catch (e) {
        logError(e, 'Artemis: app lookup failed');
    }
    return null;
}

function onScreen(actor) {
    const [x, y] = actor.get_transformed_position();
    const [w, h] = actor.get_transformed_size();
    if (!(w > 0 && h > 0)) return null;
    const m = Main.layoutManager.primaryMonitor;
    const cx = x + w / 2, cy = y + h / 2;
    if (cx < m.x || cy < m.y || cx > m.x + m.width || cy > m.y + m.height) return null;
    return {x: cx, y: cy, actor};
}

// A shown actor in the shell's chrome that passes test, depth first. The Ubuntu Dock's icons carry
// .app (they are AppIcons); the overview's dash and app grid are unmapped outside the overview.
function findActor(test) {
    const walk = (actor, depth) => {
        if (!actor || depth > MAX_DEPTH || !actor.visible) return null;
        if (actor.mapped && test(actor)) {
            const spot = onScreen(actor);
            if (spot) return spot;
        }
        for (const child of actor.get_children()) {
            const hit = walk(child, depth + 1);
            if (hit) return hit;
        }
        return null;
    };
    try {
        return walk(Main.layoutManager.uiGroup, 0);
    } catch (e) {
        logError(e, 'Artemis: dock lookup failed');
        return null;
    }
}

/** Where to press for an app: its dock icon, else the dock's "Show Apps" button, else null. */
export function dockSpot(app) {
    return findActor((a) => a.app === app) ??
        findActor((a) => typeof a.has_style_class_name === 'function' && a.has_style_class_name('show-apps'));
}

/** Plays the icon's own launch zoom, when it has one (AppIcon.animateLaunch). */
export function pressIcon(spot) {
    try {
        spot?.actor?.animateLaunch?.();
    } catch (e) {
        // only a look
    }
}

/**
 * Calls back once with the app's first new normal window (by the window tracker, or a title that
 * names the target), when it is on screen, or with null after timeoutMs. Returns a cancel function.
 */
export function waitForWindow(app, target, timeoutMs, callback) {
    const tracker = Shell.WindowTracker.get_default();
    const before = new Set(app?.get_windows?.() ?? []);
    let done = false, createdId = 0, timer = 0;
    const polls = new Set();
    const cleanup = () => {
        done = true;
        if (createdId) global.display.disconnect(createdId);
        if (timer) GLib.source_remove(timer);
        for (const id of polls) GLib.source_remove(id);
        polls.clear();
        createdId = timer = 0;
    };
    const finish = (win) => {
        if (done) return;
        cleanup();
        callback(win);
    };
    const ours = (win) => win && !before.has(win) && win.get_window_type() === Meta.WindowType.NORMAL &&
        ((app && tracker.get_window_app(win) === app) || appMatches(target, {id: win.get_wm_class() ?? '', name: win.get_title() ?? ''}));
    // A new window has no size or tracker app for a moment: check it a few times before giving up on it.
    const watch = (win) => {
        // Menus, tooltips and popups are never the app's window: skip them before polling.
        if (!win || win.get_window_type() !== Meta.WindowType.NORMAL) return;
        let tries = 0;
        let unmanaged = false;
        const unmanagedId = win.connect('unmanaged', () => { unmanaged = true; });
        const stop = (id) => { polls.delete(id); if (!unmanaged) win.disconnect(unmanagedId); };
        const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 100, () => {
            if (unmanaged) { polls.delete(id); return GLib.SOURCE_REMOVE; } // closed mid-poll
            const r = win.get_frame_rect();
            if (ours(win) && r.width > 0 && win.get_compositor_private()?.visible) {
                stop(id);
                finish(win);
                return GLib.SOURCE_REMOVE;
            }
            if (++tries > 30) { stop(id); return GLib.SOURCE_REMOVE; }
            return GLib.SOURCE_CONTINUE;
        });
        polls.add(id);
    };
    createdId = global.display.connect('window-created', (_d, win) => watch(win));
    // A fast app may already have a new window (tracked late, or created before the cue arrived).
    for (const win of app?.get_windows?.() ?? []) if (!before.has(win)) watch(win);
    timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, timeoutMs, () => { timer = 0; finish(null); return GLib.SOURCE_REMOVE; });
    return cleanup; // canceled: no callback
}
