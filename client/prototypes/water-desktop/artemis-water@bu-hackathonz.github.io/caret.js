// Where the text cursor (caret) is, so the agent's droplet can ride it while Artemis types.
//
// Uses AT-SPI, the accessibility bus screen readers use. It remembers the last text object that
// took focus or moved its caret, and asks it for the caret's character extents. On Wayland apps
// report positions relative to their own window, so those are offset by the window's buffer rect.
// position() returns null when it can't tell (the app exposes no caret, or the remembered object
// belongs to another window); the caller then falls back to the window's centre.
//
// AT-SPI calls are synchronous D-Bus round trips on the shell's main loop, so the timeout is kept
// short: a hung app can stall a frame for at most TIMEOUT_MS, never the whole desktop.

import Atspi from 'gi://Atspi';

const TIMEOUT_MS = 150;
const EMPTY_LINE_PX = 20;   // an empty text field reports no character box: assume a line this tall

export class CaretTracker {
    constructor() {
        this._acc = null;
        this._listener = null;
        try {
            Atspi.init();
            Atspi.set_timeout(TIMEOUT_MS, -1);
            this._listener = Atspi.EventListener.new((event) => {
                // A caret move, or something gaining focus (detail1 = 1); losing focus is ignored.
                if (event.type.startsWith('object:text-caret-moved') || event.detail1) this._acc = event.source;
            });
            this._listener.register('object:text-caret-moved');
            this._listener.register('object:state-changed:focused');
        } catch (e) {
            console.warn(`Artemis: no caret tracking (${e.message})`);
            this._listener = null;
        }
    }

    destroy() {
        try {
            this._listener?.deregister('object:text-caret-moved');
            this._listener?.deregister('object:state-changed:focused');
        } catch { /* already gone */ }
        this._listener = null;
        this._acc = null;
    }

    /** The caret in stage coordinates for the focused window, as {x, y (line centre), height}, or null. */
    position(win) {
        try {
            const acc = this._acc;
            if (!acc || !win) return null;
            // The remembered object must belong to the focused window's app, or the droplet would fly
            // to a stale caret in another app.
            const accPid = acc.get_process_id(), winPid = win.get_pid();
            if (accPid > 0 && winPid > 0 && accPid !== winPid) return null;
            const text = acc.get_text_iface();
            if (!text) return null;
            const origin = win.get_buffer_rect();
            const offset = text.get_caret_offset();
            let r = text.get_character_extents(offset, Atspi.CoordType.WINDOW);
            let x = r.x;
            if ((r.width <= 0 || r.height <= 0) && offset > 0) { // at the end of the text: just after the last character
                r = text.get_character_extents(offset - 1, Atspi.CoordType.WINDOW);
                x = r.x + r.width;
            }
            if (r.height <= 0) { // an empty field: the start of the text area
                const box = acc.get_component_iface()?.get_extents(Atspi.CoordType.WINDOW);
                if (!box || box.width <= 0) return null;
                const h = Math.min(box.height, EMPTY_LINE_PX);
                return {x: origin.x + box.x + 2, y: origin.y + box.y + h / 2, height: h};
            }
            return {x: origin.x + x, y: origin.y + r.y + r.height / 2, height: r.height};
        } catch {
            return null;
        }
    }
}
