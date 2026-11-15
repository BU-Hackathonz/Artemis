// The connection from the GNOME Shell extension to the Artemis core (docs/contracts.md §1, §2, §6).
// Imports only GLib, Gio and Soup, so the test script can run it under plain gjs -m.
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Soup from 'gi://Soup?version=3.0';

const BACKOFF_MS = [500, 1000, 2000, 4000, 8000];
const URL_PATTERN = /^http:\/\/127\.0\.0\.1:([1-9]\d{0,4})$/;
// A bearer token (RFC 6750 b64token). start.sh makes 43 base64url characters; anything else, such as
// a space or a line break, would be refused by Soup with a warning that prints the header.
const TOKEN_PATTERN = /^[A-Za-z0-9._~+/-]+=*$/;
const MAX_TOKEN = 512;
const MAX_LAUNCH_BYTES = 64 * 1024; // read on the shell's main thread, so keep it small
const MAX_BLOCK_LINES = 10000;
const decoder = new TextDecoder();
const encoder = new TextEncoder();

// Pure. Takes the lines of one SSE block (the last one is the blank line that ends it) and returns
// {id, data} for each complete event. Comment lines (':' first) are ignored; data lines join with '\n'.
export function parseSse(lines) {
    const events = [];
    let id = null;
    let data = [];
    for (const raw of lines) {
        const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw;
        if (line === '') {
            if (data.length > 0)
                events.push({id, data: data.join('\n')});
            id = null;
            data = [];
        } else if (!line.startsWith(':')) {
            const colon = line.indexOf(':');
            const field = colon < 0 ? line : line.slice(0, colon);
            let value = colon < 0 ? '' : line.slice(colon + 1);
            if (value.startsWith(' '))
                value = value.slice(1);
            if (field === 'id')
                id = value;
            else if (field === 'data')
                data.push(value);
        }
    }
    return events;
}

export function defaultLaunchPath() {
    const runtime = GLib.getenv('XDG_RUNTIME_DIR');
    const uid = new Gio.Credentials().get_unix_user();
    const dir = runtime ? `${runtime}/artemis` : `/tmp/artemis-${uid}`;
    return `${dir}/launch.json`;
}

// Gio's async calls in this build take a callback, so each one is wrapped as a promise.
function call(source, method, finish, ...args) {
    return new Promise((resolve, reject) => {
        source[method](...args, (src, res) => {
            try {
                resolve(src[finish](res));
            } catch (e) {
                reject(e);
            }
        });
    });
}

function isPlainObject(value) {
    return value !== null && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype;
}

// Maps a failed #request to the voice settings error codes. No response means no connection.
function voiceError(r) {
    if (!r)
        return {ok: false, error: 'not_connected'};
    const j = r.json;
    const flat = (r.status === 409 && j?.error === 'set_by_environment') ||
        (r.status === 422 && j?.error === 'invalid_setting');
    if (!flat)
        return {ok: false, error: 'error'};
    const result = {ok: false, error: j.error};
    if (typeof j.field === 'string')
        result.field = j.field;
    return result;
}

function note(message) {
    // Never called with the token or the file contents.
    if (typeof log === 'function')
        log(`Artemis: ${message}`);
}

function sha256(bytes) {
    return GLib.compute_checksum_for_bytes(GLib.ChecksumType.SHA256, GLib.Bytes.new(bytes));
}

function randomHex() {
    let hex = '';
    for (let i = 0; i < 4; i++)
        hex += (GLib.random_int() >>> 0).toString(16).padStart(8, '0');
    return hex;
}

function statLaunch(file) {
    return file.query_info(
        'standard::type,standard::size,unix::uid,unix::mode,unix::inode',
        Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null);
}

// Returns {ok: true, url, token, sig} or {ok: false, reason}. The reason never holds the token.
function readLaunch(path) {
    const reject = reason => ({ok: false, reason});
    const file = Gio.File.new_for_path(path);
    let before;
    try {
        before = statLaunch(file);
    } catch {
        return reject('launch file is missing');
    }
    if (before.get_file_type() !== Gio.FileType.REGULAR)
        return reject('launch file is not a regular file');
    if (before.get_attribute_uint32('unix::uid') !== new Gio.Credentials().get_unix_user())
        return reject('launch file belongs to another user');
    if ((before.get_attribute_uint32('unix::mode') & 0o777) !== 0o600)
        return reject('launch file mode is not 0600');
    if (before.get_size() > MAX_LAUNCH_BYTES)
        return reject('launch file is too large');

    let bytes;
    try {
        const [, contents] = file.load_contents(null);
        bytes = contents;
    } catch {
        return reject('launch file cannot be read');
    }
    // Stat again: if the path now names another file, the contents read above are not trusted.
    let after;
    try {
        after = statLaunch(file);
    } catch {
        return reject('launch file changed while it was read');
    }
    if (after.get_file_type() !== Gio.FileType.REGULAR ||
        after.get_attribute_uint64('unix::inode') !== before.get_attribute_uint64('unix::inode') ||
        after.get_attribute_uint32('unix::uid') !== before.get_attribute_uint32('unix::uid') ||
        (after.get_attribute_uint32('unix::mode') & 0o777) !== 0o600)
        return reject('launch file changed while it was read');
    if (bytes.length > MAX_LAUNCH_BYTES)
        return reject('launch file is too large');

    let json;
    try {
        json = JSON.parse(decoder.decode(bytes));
    } catch {
        return reject('launch file is not valid JSON');
    }
    if (!json || typeof json !== 'object' || typeof json.token !== 'string' || json.token === '')
        return reject('launch file has no token');
    if (json.token.length > MAX_TOKEN || !TOKEN_PATTERN.test(json.token))
        return reject('launch file token is not a valid bearer token');
    const port = typeof json.url === 'string' ? URL_PATTERN.exec(json.url)?.[1] : null;
    if (!port || Number(port) > 65535)
        return reject('launch file url is not a 127.0.0.1 address');
    return {ok: true, url: json.url, token: json.token, sig: sha256(bytes)};
}

export class CoreClient {
    #onEvent;
    #onConnected;
    #path;
    #http = new Soup.Session();
    #destroyed = false;
    #started = false;
    #gen = 0;
    #cancel = null;
    #launch = null; // {url, token}, only while a launch file is valid
    #sig = null; // digest of the launch file the session belongs to
    #sessionId = null;
    #lastSeq = 0;
    #connected = false;
    #backoff = 0;
    #lastNote = null;
    #monitor = null;
    #debounce = 0;
    #timers = new Set();

    constructor({onEvent, onConnected, launchPath = null} = {}) {
        this.#onEvent = onEvent ?? (() => {});
        this.#onConnected = onConnected ?? (() => {});
        this.#path = launchPath ?? defaultLaunchPath();
    }

    get connected() {
        return this.#connected;
    }

    get sessionId() {
        return this.#connected ? this.#sessionId : null;
    }

    start() {
        if (this.#destroyed || this.#started)
            return;
        this.#started = true;
        this.#watch();
        this.#restart();
    }

    destroy() {
        if (this.#destroyed)
            return;
        this.#destroyed = true;
        this.#gen++;
        this.#cancel?.cancel();
        this.#http.abort();
        for (const id of this.#timers)
            GLib.source_remove(id);
        this.#timers.clear();
        if (this.#debounce)
            GLib.source_remove(this.#debounce);
        this.#debounce = 0;
        this.#monitor?.cancel();
        this.#monitor = null;
        this.#launch = null;
        this.#sessionId = null;
        this.#connected = false;
    }

    async submitTask(text) {
        if (!this.#connected || !this.#launch || !this.#sessionId)
            return {ok: false, reason: 'not_connected'};
        if (typeof text !== 'string')
            return {ok: false, reason: 'error'};
        const r = await this.#request('POST', `/sessions/${encodeURIComponent(this.#sessionId)}/tasks`, {
            client_request_id: `cr_${randomHex()}`,
            source: 'keyboard',
            text,
        });
        if (r?.status === 200 && typeof r.json?.task_id === 'string')
            return {ok: true, taskId: r.json.task_id};
        if (r?.status === 409 && r.json?.error === 'task_active') {
            const result = {ok: false, reason: 'task_active'};
            if (typeof r.json.task_id === 'string')
                result.taskId = r.json.task_id;
            return result;
        }
        return {ok: false, reason: 'error'};
    }

    async decide(approvalId, decision, approvalHash) {
        const r = await this.#request('POST', `/approvals/${encodeURIComponent(approvalId)}`,
            {decision, approval_hash: approvalHash});
        if (r?.status === 204)
            return 'ok';
        if (r?.status === 409)
            return 'stale';
        return 'error';
    }

    async listen(action) {
        if (action !== 'start' && action !== 'stop')
            return false;
        return this.#expectNoContent('POST', '/voice/listen', {action});
    }

    async cancel(taskId) {
        return this.#expectNoContent('POST', `/tasks/${encodeURIComponent(taskId)}/cancel`, {});
    }

    async ready(shortcut = null) {
        if (!this.#sessionId)
            return false;
        const body = shortcut === null ? {} : {shortcut};
        return this.#expectNoContent('POST', `/sessions/${encodeURIComponent(this.#sessionId)}/ready`, body);
    }

    async diagnostics(code, shortcut = null) {
        if (!this.#sessionId)
            return false;
        const body = shortcut === null ? {code} : {code, shortcut};
        return this.#expectNoContent('POST', `/sessions/${encodeURIComponent(this.#sessionId)}/diagnostics`, body);
    }

    // Voice settings (docs/contracts.md §7). Each resolves to {ok, ...} and never throws.
    async getVoices() {
        const r = await this.#request('GET', '/voices');
        if (r?.status === 200 && Array.isArray(r.json?.voices))
            return {ok: true, voices: r.json.voices};
        return voiceError(r);
    }

    async getVoiceSettings() {
        const r = await this.#request('GET', '/settings/voice');
        if (r?.status === 200 && isPlainObject(r.json))
            return {ok: true, settings: r.json};
        return voiceError(r);
    }

    // Sends only the fields the caller gave (undefined is dropped).
    async patchVoiceSettings(patch) {
        if (!isPlainObject(patch))
            return {ok: false, error: 'invalid_setting', field: 'body'};
        const body = {};
        for (const [key, value] of Object.entries(patch)) {
            if (value !== undefined)
                body[key] = value;
        }
        const r = await this.#request('PATCH', '/settings/voice', body);
        if (r?.status === 200 && isPlainObject(r.json))
            return {ok: true, settings: r.json};
        return voiceError(r);
    }

    async #expectNoContent(method, path, body) {
        const r = await this.#request(method, path, body);
        return r?.status === 204;
    }

    // Plain request. Resolves to {status, json} or null when there is no launch or the request failed.
    async #request(method, path, body) {
        const launch = this.#launch;
        if (this.#destroyed || !launch)
            return null;
        const msg = Soup.Message.new(method, launch.url + path);
        if (!msg)
            return null;
        msg.get_request_headers().append('Authorization', `Bearer ${launch.token}`);
        if (body !== undefined)
            msg.set_request_body_from_bytes('application/json', GLib.Bytes.new(encoder.encode(JSON.stringify(body))));
        try {
            const bytes = await call(this.#http, 'send_and_read_async', 'send_and_read_finish',
                msg, GLib.PRIORITY_DEFAULT, this.#cancel);
            const data = bytes.get_data() ?? new Uint8Array();
            let json = null;
            try {
                json = JSON.parse(decoder.decode(data));
            } catch {
                json = null;
            }
            return {status: msg.get_status(), json};
        } catch {
            return null;
        }
    }

    #live(gen) {
        return !this.#destroyed && gen === this.#gen;
    }

    #setConnected(on, sid = null) {
        if (this.#destroyed)
            return;
        if (on === this.#connected)
            return;
        this.#connected = on;
        try {
            this.#onConnected(on, on ? sid : null);
        } catch {
            note('connection handler failed');
        }
    }

    #sleep(ms) {
        return new Promise(resolve => {
            const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
                this.#timers.delete(id);
                resolve();
                return GLib.SOURCE_REMOVE;
            });
            this.#timers.add(id);
        });
    }

    #nextDelay() {
        const ms = BACKOFF_MS[Math.min(this.#backoff, BACKOFF_MS.length - 1)];
        this.#backoff++;
        return ms;
    }

    #watch() {
        try {
            this.#monitor = Gio.File.new_for_path(this.#path)
                .monitor_file(Gio.FileMonitorFlags.NONE, null);
            this.#monitor.connect('changed', () => this.#scheduleRestart());
        } catch {
            // The connect loop re-reads the file every second, so this is only a faster path.
            this.#monitor = null;
        }
    }

    #scheduleRestart() {
        if (this.#destroyed || this.#debounce)
            return;
        this.#debounce = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 150, () => {
            this.#debounce = 0;
            this.#restart();
            return GLib.SOURCE_REMOVE;
        });
    }

    #restart() {
        if (this.#destroyed)
            return;
        this.#gen++;
        this.#cancel?.cancel();
        this.#cancel = new Gio.Cancellable();
        this.#setConnected(false);
        this.#loop(this.#gen, this.#cancel).catch(() => note('connector stopped unexpectedly'));
    }

    #reject(reason) {
        this.#launch = null;
        this.#setConnected(false);
        if (reason !== this.#lastNote) {
            this.#lastNote = reason;
            note(`core connection refused: ${reason}`);
        }
    }

    async #loop(gen, cancel) {
        let delay = 0;
        while (this.#live(gen)) {
            if (delay > 0) {
                await this.#sleep(delay);
                if (!this.#live(gen))
                    return;
            }
            const launch = readLaunch(this.#path);
            if (!launch.ok) {
                this.#reject(launch.reason);
                delay = 1000;
                continue;
            }
            this.#lastNote = null;
            if (launch.sig !== this.#sig) {
                this.#sig = launch.sig;
                this.#sessionId = null;
                this.#lastSeq = 0;
            }
            this.#launch = {url: launch.url, token: launch.token};

            if (!this.#sessionId) {
                const sid = await this.#createSession(cancel);
                if (!this.#live(gen))
                    return;
                if (!sid) {
                    delay = this.#nextDelay();
                    continue;
                }
                this.#sessionId = sid;
                this.#lastSeq = 0;
            }

            const outcome = await this.#stream(gen, cancel, this.#sessionId);
            if (!this.#live(gen))
                return;
            this.#setConnected(false);
            if (outcome === 'gone') {
                // The core restarted: its sessions are gone, so start a new one at once.
                this.#sessionId = null;
                this.#lastSeq = 0;
                delay = 0;
            } else {
                delay = this.#nextDelay();
            }
        }
    }

    async #createSession(cancel) {
        const launch = this.#launch;
        if (!launch)
            return null;
        const msg = Soup.Message.new('POST', `${launch.url}/sessions`);
        if (!msg)
            return null;
        msg.get_request_headers().append('Authorization', `Bearer ${launch.token}`);
        msg.set_request_body_from_bytes('application/json', GLib.Bytes.new(encoder.encode('{}')));
        try {
            const bytes = await call(this.#http, 'send_and_read_async', 'send_and_read_finish',
                msg, GLib.PRIORITY_DEFAULT, cancel);
            const status = msg.get_status();
            if (status < 200 || status >= 300)
                return null;
            const json = JSON.parse(decoder.decode(bytes.get_data() ?? new Uint8Array()));
            return typeof json?.session_id === 'string' && json.session_id !== '' ? json.session_id : null;
        } catch {
            return null;
        }
    }

    // Reads one SSE stream until it ends. Resolves to 'gone' (404: the session is unknown),
    // 'failed' (could not open) or 'ended' (the stream closed or broke after it opened).
    async #stream(gen, cancel, sid) {
        const launch = this.#launch;
        if (!launch || this.#destroyed)
            return 'failed';
        const msg = Soup.Message.new('GET', `${launch.url}/sessions/${encodeURIComponent(sid)}/events`);
        if (!msg)
            return 'failed';
        const headers = msg.get_request_headers();
        headers.append('Authorization', `Bearer ${launch.token}`);
        headers.append('Accept', 'text/event-stream');
        if (this.#lastSeq > 0)
            headers.append('Last-Event-ID', String(this.#lastSeq));

        let body;
        try {
            body = await call(this.#http, 'send_async', 'send_finish', msg, GLib.PRIORITY_DEFAULT, cancel);
        } catch {
            return 'failed';
        }
        try {
            const status = msg.get_status();
            if (status === 404)
                return 'gone';
            if (status !== 200)
                return 'failed';
            this.#backoff = 0;
            this.#setConnected(true, sid);

            const reader = Gio.DataInputStream.new(body);
            let block = [];
            for (;;) {
                const [line] = await call(reader, 'read_line_async', 'read_line_finish',
                    GLib.PRIORITY_DEFAULT, cancel);
                if (line === null || !this.#live(gen))
                    return 'ended';
                const text = typeof line === 'string' ? line : decoder.decode(line);
                if (text.replace(/\r$/, '') === '') {
                    for (const record of parseSse([...block, '']))
                        this.#deliver(gen, record);
                    block = [];
                } else {
                    if (block.length >= MAX_BLOCK_LINES)
                        return 'failed';
                    block.push(text);
                }
            }
        } catch {
            return 'ended';
        } finally {
            try {
                body.close(null);
            } catch {
                // already closed
            }
        }
    }

    #deliver(gen, record) {
        if (!this.#live(gen))
            return;
        let event;
        try {
            event = JSON.parse(record.data);
        } catch {
            return;
        }
        if (!event || typeof event.seq !== 'number' || event.seq <= this.#lastSeq)
            return;
        this.#lastSeq = event.seq;
        try {
            this.#onEvent(event);
        } catch {
            note('event handler failed');
        }
    }
}
