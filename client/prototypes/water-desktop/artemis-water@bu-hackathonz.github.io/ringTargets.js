// Where the agent's ring goes for a ring.cue (contracts.md §10). Pure: no GNOME imports, so
// client/tests/ringTargets.test.js can import it.
//
// A cue's target is one of: "editor", "terminal", "file" (the editor window), "browser", a window's
// app id or title (an app or file name), or nothing (the screen centre). The action "done" sends the
// ring home. Windows are given bottom to top, as the stacking order is, so the topmost match wins;
// an app-id match is preferred over a title match.

const KIND_PATTERNS = {
    editor: {
        app: /^(code|codium|vscodium|gedit|org\.gnome\.texteditor|sublime[_-]?text)(\.desktop)?$/i,
        title: /visual studio code|vs ?code|vscodium|text ?editor|gedit|sublime text/i,
    },
    terminal: {
        app: /terminal|konsole|kitty|alacritty|xterm|tilix|wezterm|org\.gnome\.console/i,
        title: /terminal|console/i,
    },
    browser: {
        app: /firefox|chrom|brave|epiphany|vivaldi|opera|microsoft-edge/i,
        title: /firefox|chrom|brave|epiphany|vivaldi|opera|browser/i,
    },
};

// Target words that name a kind of window. "file" means the file the agent edits: the editor.
const KIND_OF_TARGET = {editor: 'editor', file: 'editor', terminal: 'terminal', browser: 'browser'};

const centreOf = (screen) => ({x: screen.x + screen.width / 2, y: screen.y + screen.height / 2, width: 0, height: 0});

// The topmost window (last in stacking order) whose app id, then whose title, matches.
function topmost(windows, test) {
    for (let i = windows.length - 1; i >= 0; i--) if (test(windows[i], 'app')) return windows[i];
    for (let i = windows.length - 1; i >= 0; i--) if (test(windows[i], 'title')) return windows[i];
    return null;
}

function findKind(windows, kind) {
    const re = KIND_PATTERNS[kind];
    return topmost(windows, (w, field) => re[field].test(field === 'app' ? w.appId ?? '' : w.title ?? ''));
}

function findNamed(windows, name) {
    const q = name.toLowerCase();
    return topmost(windows, (w, field) => (field === 'app' ? w.appId ?? '' : w.title ?? '').toLowerCase().includes(q));
}

/**
 * cue: {action, target?}; windows: [{appId, title, rect: {x, y, width, height}}] bottom to top;
 * screen: {x, y, width, height}; home: {x, y} where the ring rests (the island's ring).
 * Returns a rectangle {x, y, width, height}: the window's rect, or a point (zero size) for home,
 * the screen centre, or the centre of nothing that matches.
 */
export function cueTargetRect(cue, {windows = [], screen, home} = {}) {
    if (cue?.action === 'done') return home ? {x: home.x, y: home.y, width: 0, height: 0} : centreOf(screen);
    const target = typeof cue?.target === 'string' ? cue.target.trim() : '';
    if (!target) return centreOf(screen);

    const kind = KIND_OF_TARGET[target.toLowerCase()];
    const win = kind ? findKind(windows, kind) : findNamed(windows, target);
    if (!win) return centreOf(screen);
    return {x: win.rect.x, y: win.rect.y, width: win.rect.width, height: win.rect.height};
}
