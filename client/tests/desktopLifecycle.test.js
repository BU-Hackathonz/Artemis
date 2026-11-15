import {readFileSync} from 'node:fs';
import {describe, expect, test} from 'vitest';

// Exercise the production Typist with deterministic Shell/Clutter doubles, without a desktop.
const source = readFileSync(new URL('../prototypes/water-desktop/artemis-water@bu-hackathonz.github.io/desktop.js', import.meta.url), 'utf8')
    .replace(/^import .*;\n/gm, '').replace(/export /g, '');
function fixture() {
    const events = [], timers = [];
    const GLib = {PRIORITY_DEFAULT: 0, SOURCE_REMOVE: false, get_monotonic_time: () => 1,
        timeout_add: (_priority, _ms, callback) => timers.push(callback)};
    const Clutter = {KeyState: {PRESSED: 1, RELEASED: 0}, unicode_to_keysym: n => n};
    const Main = {modalCount: 0};
    const win = {title: 'Document', get_title() { return this.title; }};
    const global = {display: {focus_window: win}};
    const Shell = {WindowTracker: {get_default: () => ({get_window_app: () => ({get_id: () => 'editor'})})}};
    const Typist = new Function('GLib', 'Clutter', 'Main', 'global', 'Shell', `${source}; return Typist;`)(GLib, Clutter, Main, global, Shell);
    const typist = new Typist({notify_keyval: (_time, key, state) => events.push([key, state])});
    return {typist, events, timers, win, global};
}
// Runs queued sleeps until the job settles.
async function drain(timers, job) {
    let done = false;
    job.then(() => { done = true; });
    for (let i = 0; i < 100 && !done; i++) {
        await Promise.resolve();
        if (timers.length) timers.shift()();
        await Promise.resolve();
    }
    return job;
}
describe('desktop typing lifecycle', () => {
    test('cancel releases held keys and a new task cannot revive the old job', async () => {
        const {typist, events, timers} = fixture();
        const job = typist.run('type', 'ab', 'editor: Document');
        expect(events).toEqual([[97, 1]]);
        typist.stop();
        expect(events).toContainEqual([97, 0]);
        typist.resume();
        timers.shift()(); await Promise.resolve();
        timers.shift()(); await Promise.resolve();
        expect((await job).ok).toBe(false);
        expect(events.some(([key, state]) => key === 98 && state === 1)).toBe(false);
    });
    test('destroy during a held key releases it once and ends the job', async () => {
        const {typist, events, timers} = fixture();
        const job = typist.run('type', 'ab', 'editor: Document');
        typist.destroy();
        timers.shift()(); await Promise.resolve();
        timers.shift()(); await Promise.resolve();
        expect((await job).ok).toBe(false);
        expect(events).toEqual([[97, 1], [97, 0]]);
    });
    test('queued action from an old connection cannot run after reconnect', async () => {
        const {typist, events} = fixture();
        const generation = typist.generation;
        typist.stop(); typist.resume();
        expect((await typist.run('type', 'a', 'editor: Document', generation)).ok).toBe(false);
        expect(events).toEqual([]);
    });
    test('a title change while typing (an editor marking the document modified) does not stop the job', async () => {
        const {typist, events, timers, win} = fixture();
        const job = typist.run('type', 'hello', 'editor: Document');
        win.title = '*Document';
        expect(await drain(timers, job)).toEqual({ok: true});
        expect(events.filter(([, state]) => state === 1).map(([key]) => key)).toEqual([104, 101, 108, 108, 111]);
    });
    test('focus moving to another window stops typing', async () => {
        const {typist, events, timers, global} = fixture();
        const job = typist.run('type', 'hello', 'editor: Document');
        global.display.focus_window = {get_title: () => 'Document'};
        const result = await drain(timers, job);
        expect(result.ok).toBe(false);
        expect(result.message).toMatch(/focus moved/);
        expect(events.filter(([, state]) => state === 1)).toEqual([[104, 1]]);
    });
});
