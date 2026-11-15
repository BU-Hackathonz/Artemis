// Where the text cursor is, so the agent's ball can ride it while a command types.
// Uses AT-SPI (the accessibility bus screen readers use): it remembers the last focused or
// caret-moving text object and asks it for the caret's character extents. On Wayland apps report
// positions relative to their own window, so window coordinates are offset by the focused window's
// surface. Returns null when the app doesn't expose its caret; callers then simulate the writing.

import Atspi from 'gi://Atspi';

export class CaretTracker {
    constructor() {
        this._acc = null;
        try {
            Atspi.init();
            this._listener = Atspi.EventListener.new((event) => {
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

    /** Stage position just after the caret, or null. */
    position() {
        try {
            const text = this._acc?.get_text_iface();
            if (!text) return null;
            const offset = text.get_caret_offset();
            let r = text.get_character_extents(offset, Atspi.CoordType.WINDOW);
            let x = r.x;
            if (r.width <= 0 && offset > 0) { // end of the text: measure the previous character
                r = text.get_character_extents(offset - 1, Atspi.CoordType.WINDOW);
                x = r.x + r.width;
            }
            if (r.height <= 0) return null;
            const win = global.display.focus_window;
            const origin = win ? win.get_buffer_rect() : {x: 0, y: 0};
            return {x: origin.x + x + 6, y: origin.y + r.y + r.height / 2};
        } catch {
            return null;
        }
    }
}
