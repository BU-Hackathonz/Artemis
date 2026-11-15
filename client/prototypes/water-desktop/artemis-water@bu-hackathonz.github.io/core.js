// The island as a client of main's core (docs/contracts.md §1, §2, §6), for the Ubuntu demo.
//
// infra/start.sh writes $XDG_RUNTIME_DIR/artemis/launch.json ({url, token, pid}) while the core runs.
// The island watches for it, checks the address is loopback, opens one session, follows the session's
// event stream (SSE, replayed from Last-Event-ID after a drop), and sends push to talk, approvals and
// cancel. The token is read from that file only: never logged, spoken, put in a URL or in an event.
// Requests carry no Origin header, which the core accepts with a valid token (§6).
// When the core is not running the island stays in its own demo mode.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Soup from 'gi://Soup?version=3.0';

const LAUNCH = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'artemis', 'launch.json']);

export class CoreClient {
    /** handlers: {onConnected(bool), onEvent(event)} */
    constructor(handlers) {
        this._h = handlers;
        this._http = new Soup.Session({timeout: 10});
        this._sse = new Soup.Session({timeout: 0, idle_timeout: 0});
        this._launch = null;
        this._sid = null;
        this._lastId = null;
        this._connected = false;
        const dir = Gio.File.new_for_path(GLib.path_get_dirname(LAUNCH));
        try { dir.make_directory_with_parents(null); } catch (_) { /* exists */ }
        this._monitor = dir.monitor_directory(Gio.FileMonitorFlags.NONE, null);
        this._monitor.connect('changed', () => this._scheduleCheck(300));
        this._scheduleCheck(0);
    }

    get connected() { return this._connected; }

    destroy() {
        this._dead = true;
        this._monitor?.cancel();
        if (this._checkId) GLib.source_remove(this._checkId);
        if (this._retryId) GLib.source_remove(this._retryId);
        this._sseCancel?.cancel();
        this._http.abort();
        this._sse.abort();
    }

    // ----- requests (contracts §1) -----

    talk(start) { return this._post('/voice/listen', {action: start ? 'start' : 'stop'}); }
    ready(shortcut) { return this._sid ? this._post(`/sessions/${this._sid}/ready`, {shortcut}) : Promise.resolve(); }
    approve(a, approve) { return this._post(`/approvals/${a.approval_id}`, {decision: approve ? 'approve' : 'reject', approval_hash: a.approval_hash}); }
    cancel(taskId) { return this._post(`/tasks/${taskId}/cancel`, {}); }

    // ----- connection -----

    _scheduleCheck(ms) {
        if (this._checkId) GLib.source_remove(this._checkId);
        this._checkId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { this._checkId = 0; this._check(); return GLib.SOURCE_REMOVE; });
    }

    _readLaunch() {
        try {
            const [ok, bytes] = GLib.file_get_contents(LAUNCH);
            if (!ok) return null;
            const j = JSON.parse(new TextDecoder().decode(bytes));
            const u = GLib.Uri.parse(j.url, GLib.UriFlags.NONE);
            if (!['127.0.0.1', 'localhost', '::1'].includes(u.get_host())) return null; // loopback only (§6)
            if (typeof j.token !== 'string' || !j.token) return null;
            return {url: j.url.replace(/\/$/, ''), token: j.token, pid: j.pid};
        } catch (_) {
            return null;
        }
    }

    async _check() {
        if (this._dead) return;
        const launch = this._readLaunch();
        const same = launch && this._launch && launch.url === this._launch.url && launch.token === this._launch.token;
        if (same && this._connected) return;
        this._drop();
        if (!launch) return;
        this._launch = launch;
        try {
            await this._get('/health');
            const {session_id: sid} = await this._post('/sessions', {});
            this._sid = sid;
            this._lastId = null;
            this._setConnected(true);
            this._follow();
        } catch (e) {
            console.log(`Artemis island: core not reachable yet (${e.message})`);
            this._retry();
        }
    }

    _retry() {
        if (this._retryId || this._dead) return;
        this._retryId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 3, () => { this._retryId = 0; this._check(); return GLib.SOURCE_REMOVE; });
    }

    _drop() {
        this._sseCancel?.cancel();
        this._sseCancel = null;
        this._sid = null;
        this._launch = null;
        this._setConnected(false);
    }

    _setConnected(on) {
        if (this._connected === on) return;
        this._connected = on;
        this._h.onConnected?.(on);
    }

    // ----- the event stream (contracts §2) -----

    _follow() {
        if (!this._sid || !this._launch) return;
        const msg = Soup.Message.new('GET', `${this._launch.url}/sessions/${this._sid}/events`);
        msg.request_headers.append('Authorization', `Bearer ${this._launch.token}`);
        msg.request_headers.append('Accept', 'text/event-stream');
        if (this._lastId !== null) msg.request_headers.append('Last-Event-ID', String(this._lastId));
        const cancel = this._sseCancel = new Gio.Cancellable();
        this._sse.send_async(msg, GLib.PRIORITY_DEFAULT, cancel, (s, res) => {
            let stream;
            try { stream = s.send_finish(res); } catch (_) { if (!cancel.is_cancelled()) this._lost(); return; }
            if (msg.get_status() !== 200) { this._lost(); return; }
            const lines = new Gio.DataInputStream({base_stream: stream});
            let data = [], id = null;
            const next = () => lines.read_line_async(GLib.PRIORITY_DEFAULT, cancel, (l, r) => {
                let line;
                try { [line] = l.read_line_finish_utf8(r); } catch (_) { if (!cancel.is_cancelled()) this._lost(); return; }
                if (line === null) { if (!cancel.is_cancelled()) this._lost(); return; }
                line = line.replace(/\r$/, '');
                if (line === '') {
                    if (data.length) {
                        try {
                            const ev = JSON.parse(data.join('\n'));
                            if (id !== null) this._lastId = id;
                            this._h.onEvent?.(ev);
                        } catch (e) { console.warn(`Artemis island: bad event (${e.message})`); }
                    }
                    data = []; id = null;
                } else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
                else if (line.startsWith('id:')) id = parseInt(line.slice(3), 10);
                next();
            });
            next();
        });
    }

    _lost() {
        // the stream dropped: replay from the last event seen, or reconnect from scratch if the core is gone
        if (this._dead) return;
        if (!this._readLaunch()) { this._drop(); return; }
        GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => { if (this._sid) this._follow(); else this._check(); return GLib.SOURCE_REMOVE; });
    }

    // ----- HTTP -----

    _get(path) { return this._request('GET', path, null); }
    _post(path, body) { return this._request('POST', path, body); }

    _request(method, path, body) {
        return new Promise((resolve, reject) => {
            if (!this._launch) { reject(new Error('Artemis is not running')); return; }
            const msg = Soup.Message.new(method, `${this._launch.url}${path}`);
            msg.request_headers.append('Authorization', `Bearer ${this._launch.token}`);
            if (body !== null) msg.set_request_body_from_bytes('application/json', new GLib.Bytes(new TextEncoder().encode(JSON.stringify(body))));
            this._http.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, null, (s, res) => {
                try {
                    const bytes = s.send_and_read_finish(res);
                    const status = msg.get_status();
                    const text = new TextDecoder().decode(bytes.get_data() ?? new Uint8Array());
                    if (status >= 200 && status < 300) { resolve(text ? JSON.parse(text) : {}); return; }
                    let err = `HTTP ${status}`;
                    try { const j = JSON.parse(text); err = j.error ?? j.detail?.error ?? err; } catch (_) {}
                    reject(new Error(err));
                } catch (e) {
                    reject(e);
                }
            });
        });
    }
}
