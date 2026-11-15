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

// Where the droplet rides for a caret ({x, y = line centre, height}): just above and to the right
// of it, so it marks the spot without covering the text being typed, kept inside the screen.
export function caretAnchor(caret, screen, lift = 18) {
    const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
    return {
        x: clamp(caret.x + lift, screen.x + lift, screen.x + screen.width - lift),
        y: clamp(caret.y - caret.height / 2 - lift, screen.y + lift, screen.y + screen.height - lift),
    };
}

// The window a cue names (as cueTargetRect picks it), or null when none is open: an "open" cue for an
// app with no window yet sends the droplet to the app's icon in the dock instead (launchFlight.js).
export function cueWindow(cue, windows = []) {
    const target = typeof cue?.target === 'string' ? cue.target.trim() : '';
    if (!target || cue?.action === 'done') return null;
    const kind = KIND_OF_TARGET[target.toLowerCase()];
    return kind ? findKind(windows, kind) : findNamed(windows, target);
}

// Names people and the core use for an app, to the word its launcher id or name contains.
const APP_ALIASES = {'vs code': 'code', 'vscode': 'code', 'visual studio code': 'code', 'browser': 'firefox',
    'terminal': 'terminal', 'files': 'nautilus', 'text editor': 'texteditor'};

const squash = (s) => (s ?? '').toLowerCase().replace(/\.desktop$/, '').replace(/[^a-z0-9]+/g, '');

/**
 * True when an app ({id: "code_code.desktop", name: "Visual Studio Code"}) is the one a cue's target
 * names ("Visual Studio Code", "VS Code", "code", "firefox"). Pure, for the tests.
 * The whole name, the alias, or the launcher id's last part ("code_code" -> "code") must match.
 */
export function appMatches(target, app) {
    const t = (target ?? '').trim().toLowerCase();
    if (!t || !app) return false;
    const want = squash(APP_ALIASES[t] ?? t);
    const name = squash(app.name);
    const idParts = (app.id ?? '').toLowerCase().replace(/\.desktop$/, '').split(/[._-]/).map(squash).filter(Boolean);
    return name === squash(t) || name === want || idParts.includes(want) || squash(app.id) === want;
}
