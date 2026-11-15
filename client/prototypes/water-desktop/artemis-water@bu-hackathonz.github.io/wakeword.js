// Runs wake.py (local Whisper, "Hey Artemis") as a child process and turns its JSON lines into
// callbacks. Opt-in, like main's ARTEMIS_LISTEN_MODE=wake. Everything is transcribed on this computer;
// speech that does not start with Artemis's name is dropped by the helper unread.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';

const PY = GLib.build_filenamev([GLib.get_user_data_dir(), 'artemis-wake', 'venv', 'bin', 'python']);

export class WakeWord {
    /** handlers: {onArmed(), onDisarmed(), onSpeech(), onPartial(text), onRequest(text), onError(message)} */
    constructor(dir, handlers) {
        this._script = GLib.build_filenamev([dir, 'wake.py']);
        this._h = handlers;
        this._proc = null;
        this._muted = false;
    }

    get running() { return this._proc !== null; }

    static available() { return GLib.file_test(PY, GLib.FileTest.IS_EXECUTABLE); }

    start() {
        if (this._proc) return;
        if (!WakeWord.available()) { this._h.onError?.('the wake-word helper is not installed'); return; }
        const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE});
        launcher.setenv('HF_HUB_OFFLINE', '1', true); // the model is already on disk: no network at all
        try {
            this._proc = launcher.spawnv([PY, this._script]);
        } catch (e) {
            this._h.onError?.(e.message);
            return;
        }
        this._in = this._proc.get_stdin_pipe();
        this._out = new Gio.DataInputStream({base_stream: this._proc.get_stdout_pipe()});
        this._cancel = new Gio.Cancellable();
        this._read();
        // If the listener ever stops on its own (the microphone dropped, a crash), start it again instead of
        // going silently deaf. A deliberate stop() clears _want, so it stays stopped.
        this._want = true;
        const proc = this._proc;
        proc.wait_async(null, () => {
            if (this._proc === proc) this._proc = null;
            if (!this._want) return;
            this._restarts = (this._restarts ?? 0) + 1;
            if (this._restarts > 5) { this._h.onError?.('the listener keeps stopping; turn it off and on again'); return; }
            console.log(`Artemis wake word: the listener stopped, restarting (${this._restarts})`);
            GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 2, () => { if (this._want && !this._proc) this.start(); return GLib.SOURCE_REMOVE; });
        });
        GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 60, () => { if (this._proc === proc) this._restarts = 0; return GLib.SOURCE_REMOVE; });
        if (this._muted) this._send('mute');
        this._send(this._wake === false ? 'wake off' : 'wake on');
    }

    stop() {
        this._want = false;
        if (!this._proc) return;
        this._cancel?.cancel();
        this._send('quit');
        const p = this._proc;
        this._proc = null;
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => { try { p.force_exit(); } catch (_) {} return GLib.SOURCE_REMOVE; });
    }

    /** Whether saying Artemis's name starts a request (the helper also serves push to talk). */
    setWake(on) { this._wake = on; this._send(on ? 'wake on' : 'wake off'); }

    /** Push to talk: listen now (words stream back, it stops by itself on silence); stop ends it at once. */
    listen() { this._send('listen'); }
    stopListening() { this._send('stop'); }

    /** Artemis is speaking, or push to talk has the mic: the helper hears nothing. */
    setMuted(muted) {
        if (this._muted === muted) return;
        this._muted = muted;
        this._send(muted ? 'mute' : 'unmute');
    }

    _send(line) {
        if (!this._proc || !this._in) return;
        try { this._in.write_all(new TextEncoder().encode(`${line}\n`), null); this._in.flush(null); } catch (_) { /* helper gone */ }
    }

    _read() {
        this._out.read_line_async(GLib.PRIORITY_DEFAULT, this._cancel, (s, res) => {
            let line;
            try { [line] = s.read_line_finish_utf8(res); } catch (_) { return; }
            if (line === null) return; // the helper exited
            try {
                const ev = JSON.parse(line);
                if (ev.event === 'armed') this._h.onArmed?.();
                else if (ev.event === 'disarmed') this._h.onDisarmed?.();
                else if (ev.event === 'request') this._h.onRequest?.(ev.text);
                else if (ev.event === 'partial') this._h.onPartial?.(ev.text);
                else if (ev.event === 'speech') this._h.onSpeech?.();
                else if (ev.event === 'cancel') this._h.onCancel?.();
                else if (ev.event === 'error') this._h.onError?.(ev.message);
            } catch (_) { /* not a JSON line */ }
            if (this._proc) this._read();
        });
    }
}
