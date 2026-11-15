// Artemis's spoken voice for the island, matching the core in main (server/artemis/voice/speech.py):
// ElevenLabs first, in the voice chosen in Settings or the panel menu (voices.js: the free default voices), on
// eleven_flash_v2_5, with text redacted before it is sent. If ElevenLabs refuses that voice it uses a stock voice. Without a key, or offline, it uses the system voice (speech-dispatcher), the way
// the core falls back to NVDA or SAPI on Windows. One voice at a time; `high` interrupts, like the core.
//
// Key: $ELEVENLABS_API_KEY, else the file ~/.config/elevenlabs (ELEVENLABS_API_KEY=...). It is never logged.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Soup from 'gi://Soup?version=3.0';
import Gst from 'gi://Gst?version=1.0';
import {voiceByKey} from './voices.js';

const STOCK_VOICE = 'SAz9YHcvj6GT2YYXdXww';   // River: used if the chosen voice is refused
const MODEL = 'eleven_flash_v2_5';
const URL = 'https://api.elevenlabs.io/v1/text-to-speech/%s?output_format=mp3_44100_128';

/** Strip anything that looks like a secret before text leaves the machine (a small port of policy/redact.py). */
export function redact(text) {
    return String(text)
        .replace(/\b(sk[-_](?:ant-)?[A-Za-z0-9_-]{8,})\b/g, '[redacted]')
        .replace(/\b([A-Z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD))\s*[=:]\s*\S+/gi, '$1=[redacted]')
        .replace(/\b[A-Za-z0-9_-]{32,}\b/g, '[redacted]');
}

function readKey() {
    const env = GLib.getenv('ELEVENLABS_API_KEY');
    if (env) return env.trim();
    try {
        const [ok, bytes] = GLib.file_get_contents(GLib.build_filenamev([GLib.get_user_config_dir(), 'elevenlabs']));
        if (!ok) return null;
        const txt = new TextDecoder().decode(bytes);
        const m = txt.match(/ELEVENLABS_API_KEY\s*=\s*['"]?([^'"\s#]+)/);
        return m ? m[1] : (/^[A-Za-z0-9_-]{20,}$/.test(txt.trim()) ? txt.trim() : null);
    } catch (_) {
        return null;
    }
}

export class Voice {
    /** onSpeaking(bool): the island's bars follow the voice. */
    /** getVoiceKey(): the chosen voice (voices.js key), read for every line so a switch applies at once. */
    constructor(onSpeaking = () => {}, getVoiceKey = () => 'bill') {
        this._getVoiceKey = getVoiceKey;
        if (!Gst.is_initialized()) Gst.init(null);
        this._onSpeaking = onSpeaking;
        this._session = new Soup.Session({timeout: 15});
        this._queue = [];
        this._busy = false;
        this._gen = 0; // bumps on stop(): late network replies for old lines are dropped
        this._player = null;
        this._proc = null;
        this._file = null;
        this.engine = readKey() ? 'elevenlabs' : 'system';
    }

    /** Say a line. priority 'high' interrupts what is being said (approvals, results, errors). */
    say(text, priority = 'normal') {
        if (!text || this.quiet?.()) return; // main's core is speaking for Artemis
        if (priority === 'high') this.stop();
        this._queue.push(redact(text));
        if (!this._busy) this._next();
    }

    stop() {
        this._gen++;
        this._queue = [];
        this._session.abort();
        this._endPlayback();
        if (this._proc) { try { this._proc.force_exit(); } catch (_) {} this._proc = null; }
        this._setBusy(false);
    }

    destroy() {
        this.stop();
        this._onSpeaking = () => {};
    }

    _setBusy(b) {
        if (this._busy === b) return;
        this._busy = b;
        this._onSpeaking(b);
    }

    _next() {
        const text = this._queue.shift();
        if (text === undefined) { this._setBusy(false); return; }
        this._setBusy(true);
        const key = readKey();
        const v = voiceByKey(this._getVoiceKey());
        if (key && v.id) this._eleven(text, key, this._gen, v.id);
        else this._system(text, this._gen);
    }

    _eleven(text, key, gen, voiceId, retried = false) {
        const msg = Soup.Message.new('POST', URL.replace('%s', voiceId));
        msg.request_headers.append('xi-api-key', key);
        msg.request_headers.append('Accept', 'audio/mpeg');
        const body = JSON.stringify({text, model_id: MODEL, voice_settings: {stability: 0.5, similarity_boost: 0.75, speed: 1.0}});
        msg.set_request_body_from_bytes('application/json', new GLib.Bytes(new TextEncoder().encode(body)));
        this._session.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, null, (s, res) => {
            let bytes = null;
            try { bytes = s.send_and_read_finish(res); } catch (_) { /* aborted or offline */ }
            if (gen !== this._gen) return;
            const status = msg.get_status();
            if (bytes && status === 200) { this.engine = 'elevenlabs'; this._play(bytes, gen); return; }
            // Artemis's own voice is a library voice: on a free plan the API refuses it (402/403). Use a stock voice.
            if (!retried && (status === 402 || status === 403 || status === 404) && voiceId !== STOCK_VOICE) {
                console.log(`Artemis voice: ElevenLabs refused the chosen voice (HTTP ${status}); using a stock voice`);
                this._eleven(text, key, gen, STOCK_VOICE, true);
                return;
            }
            console.log(`Artemis voice: ElevenLabs unavailable (HTTP ${status || 'no connection'}); using the system voice`);
            this._system(text, gen);
        });
    }

    _play(bytes, gen) {
        const path = GLib.build_filenamev([GLib.get_user_runtime_dir(), `artemis-voice-${GLib.get_monotonic_time()}.mp3`]);
        try {
            GLib.file_set_contents(path, bytes.get_data());
        } catch (_) {
            this._next();
            return;
        }
        this._file = path;
        this._player = Gst.ElementFactory.make('playbin', null);
        this._player.set_property('uri', GLib.filename_to_uri(path, null));
        const bus = this._player.get_bus();
        bus.add_signal_watch();
        this._busId = bus.connect('message', (_b, m) => {
            if (gen !== this._gen) return;
            if (m.type === Gst.MessageType.EOS || m.type === Gst.MessageType.ERROR) {
                this._endPlayback();
                this._next();
            }
        });
        this._player.set_state(Gst.State.PLAYING);
    }

    _endPlayback() {
        if (this._player) {
            const bus = this._player.get_bus();
            if (this._busId) bus.disconnect(this._busId);
            bus.remove_signal_watch();
            this._player.set_state(Gst.State.NULL);
            this._player = null;
            this._busId = 0;
        }
        if (this._file) { try { Gio.File.new_for_path(this._file).delete(null); } catch (_) {} this._file = null; }
    }

    _system(text, gen) {
        this.engine = 'system';
        try {
            this._proc = Gio.Subprocess.new(['spd-say', '-w', '-r', '20', text], Gio.SubprocessFlags.NONE);
            this._proc.wait_async(null, () => {
                this._proc = null;
                if (gen === this._gen) this._next();
            });
        } catch (_) {
            this._next(); // no speech-dispatcher either: stay silent, the island still shows the state
        }
    }
}
