// Push-to-talk for the island: record the microphone while the developer holds the talk control,
// then turn the speech into text with ElevenLabs speech to text (Scribe). Like main's voice
// (server/artemis/voice/listen.py), there are no partials: one transcript, on release.
// The recording lives in $XDG_RUNTIME_DIR only while it is sent, then it is deleted. Key: as voice.js.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Soup from 'gi://Soup?version=3.0';
import Gst from 'gi://Gst?version=1.0';

const STT_URL = 'https://api.elevenlabs.io/v1/speech-to-text';

function readKey() {
    const env = GLib.getenv('ELEVENLABS_API_KEY');
    if (env) return env.trim();
    try {
        const [ok, bytes] = GLib.file_get_contents(GLib.build_filenamev([GLib.get_user_config_dir(), 'elevenlabs']));
        if (!ok) return null;
        const m = new TextDecoder().decode(bytes).match(/ELEVENLABS_API_KEY\s*=\s*['"]?([^'"\s#]+)/);
        return m ? m[1] : null;
    } catch (_) {
        return null;
    }
}

export class Listener {
    constructor() {
        if (!Gst.is_initialized()) Gst.init(null);
        this._session = new Soup.Session({timeout: 30});
        this._pipe = null;
        this._file = null;
        this._t0 = 0;
    }

    get listening() { return this._pipe !== null; }

    /** Start recording. Throws if there is no microphone pipeline. */
    start() {
        if (this._pipe) return;
        this._file = GLib.build_filenamev([GLib.get_user_runtime_dir(), `artemis-talk-${GLib.get_monotonic_time()}.wav`]);
        this._pipe = Gst.parse_launch(
            `autoaudiosrc ! audioconvert ! audioresample ! audio/x-raw,rate=16000,channels=1 ! wavenc ! filesink location="${this._file}"`);
        this._pipe.set_state(Gst.State.PLAYING);
        this._t0 = GLib.get_monotonic_time();
    }

    /** Stop recording and transcribe. Resolves to the text ('' for silence or a tap). */
    stop() {
        return new Promise((resolve, reject) => {
            const pipe = this._pipe, file = this._file;
            if (!pipe) { resolve(''); return; }
            this._pipe = null;
            const held = (GLib.get_monotonic_time() - this._t0) / 1e6;
            const bus = pipe.get_bus();
            let done = false;
            const finish = () => {
                if (done) return;
                done = true;
                pipe.set_state(Gst.State.NULL);
                if (held < 0.35) { this._delete(file); resolve(''); return; } // a tap, not speech
                this._transcribe(file).then(resolve, reject).finally(() => this._delete(file));
            };
            bus.add_signal_watch();
            const id = bus.connect('message', (_b, m) => {
                if (m.type === Gst.MessageType.EOS || m.type === Gst.MessageType.ERROR) { bus.disconnect(id); bus.remove_signal_watch(); finish(); }
            });
            pipe.send_event(Gst.Event.new_eos()); // flush the wav header before reading the file
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => { finish(); return GLib.SOURCE_REMOVE; });
        });
    }

    cancel() {
        if (!this._pipe) return;
        this._pipe.set_state(Gst.State.NULL);
        this._pipe = null;
        this._delete(this._file);
    }

    destroy() {
        this.cancel();
        this._session.abort();
    }

    _transcribe(file) {
        return new Promise((resolve, reject) => {
            const key = readKey();
            if (!key) { reject(new Error('no ElevenLabs key for speech to text')); return; }
            let bytes;
            try { bytes = GLib.file_get_contents(file)[1]; } catch (e) { reject(e); return; }
            const form = new Soup.Multipart('multipart/form-data');
            form.append_form_string('model_id', 'scribe_v1');
            form.append_form_string('language_code', 'en');
            form.append_form_file('file', 'speech.wav', 'audio/wav', new GLib.Bytes(bytes));
            const msg = Soup.Message.new_from_multipart(STT_URL, form);
            msg.request_headers.append('xi-api-key', key);
            this._session.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, null, (s, res) => {
                try {
                    const body = new TextDecoder().decode(s.send_and_read_finish(res).get_data());
                    const status = msg.get_status();
                    if (status !== 200) {
                        reject(new Error(status === 401 ? 'the ElevenLabs key lacks the Speech to Text permission' : `speech to text failed (HTTP ${status})`));
                        return;
                    }
                    // drop sound tags such as [music] or (laughs): silence must not become a request
                    resolve(String(JSON.parse(body).text ?? '').replace(/\[[^\]]*\]|\([^)]*\)/g, ' ').replace(/\s+/g, ' ').trim());
                } catch (e) {
                    reject(e);
                }
            });
        });
    }

    _delete(file) {
        if (!file) return;
        try { Gio.File.new_for_path(file).delete(null); } catch (_) { /* already gone */ }
    }
}
