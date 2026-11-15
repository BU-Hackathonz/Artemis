// Tests core.js against a running Artemis core stub. Run with:
//   ARTEMIS_TEST_URL=http://127.0.0.1:8792 ARTEMIS_TEST_TOKEN=<launch token> gjs -m core-gjs-test.js
// The core must be started with the same token (see the README for the uv command).
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import System from 'system';
import {CoreClient, parseSse} from '../artemis-water@bu-hackathonz.github.io/core.js';

const URL = GLib.getenv('ARTEMIS_TEST_URL');
const TOKEN = GLib.getenv('ARTEMIS_TEST_TOKEN');
const TASK_TEXT = 'create a Python program that prints Hello World';
const DEADLINE_MS = 120000;

let failures = 0;
function check(condition, label) {
    if (condition) {
        print(`ok    ${label}`);
    } else {
        failures++;
        print(`FAIL  ${label}`);
    }
}

function sleep(ms) {
    return new Promise(resolve => {
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            resolve();
            return GLib.SOURCE_REMOVE;
        });
    });
}

async function waitFor(label, predicate, ms = 30000) {
    const start = Date.now();
    while (!predicate()) {
        if (Date.now() - start > ms)
            throw new Error(`timed out waiting for ${label}`);
        await sleep(50);
    }
}

// Writes a launch file the way infra/start.sh does: a temp file, then rename over the target.
function writeLaunch(path, {mode = 0o600, extra = {}} = {}) {
    const tmp = `${path}.tmp`;
    const body = JSON.stringify({url: URL, token: TOKEN, pid: 0, ...extra}) + '\n';
    Gio.File.new_for_path(tmp).replace_contents(
        new TextEncoder().encode(body), null, false, Gio.FileCreateFlags.PRIVATE, null);
    Gio.File.new_for_path(tmp).set_attribute_uint32('unix::mode', mode, Gio.FileQueryInfoFlags.NONE, null);
    if (GLib.rename(tmp, path) !== 0)
        throw new Error('rename failed');
}

function testParser() {
    const records = parseSse([
        ': ping',
        '',
        'id: 3',
        'data: {"seq":3,',
        'data: "x":1}',
        '',
        'id: 4\r',
        'data: {"seq":4}\r',
        '\r',
        'id: 5',
        'data: {"seq":5}',
    ]);
    check(records.length === 2, 'parser: two complete events, the unterminated one is held back');
    check(records[0].id === '3' && records[0].data === '{"seq":3,\n"x":1}',
        'parser: data lines join with newline, comments ignored');
    check(records[1].id === '4' && records[1].data === '{"seq":4}', 'parser: CRLF lines accepted');
    check(parseSse([': keep-alive', '']).length === 0, 'parser: a comment-only block gives no event');
}

async function testLive(dir) {
    const launchPath = `${dir}/launch.json`;
    writeLaunch(launchPath);

    const events = [];
    const approvals = [];
    const connectedLog = [];
    const client = new CoreClient({
        launchPath,
        onEvent: ev => {
            events.push(ev);
            if (ev.type === 'approval.requested')
                approvals.push(ev.data);
        },
        onConnected: (on, sid) => connectedLog.push({on, sid}),
    });
    client.start();
    await waitFor('first connection', () => client.connected);
    const sid1 = client.sessionId;
    check(typeof sid1 === 'string' && connectedLog.length === 1 && connectedLog[0].on === true,
        'connect: onConnected(true, sid) once for the new session');

    // Two submits sent together: one starts a task, the other finds it active.
    const [a, b] = await Promise.all([client.submitTask(TASK_TEXT), client.submitTask(TASK_TEXT)]);
    const winners = [a, b].filter(r => r.ok);
    const losers = [a, b].filter(r => !r.ok);
    check(winners.length === 1 && losers.length === 1 && losers[0].reason === 'task_active',
        'submit: one task starts and the second is refused with task_active');
    const taskId = winners[0]?.taskId;
    check(typeof taskId === 'string' && losers[0]?.taskId === taskId,
        'submit: task_active names the active task');

    // Drive the approvals and wait for the task to finish.
    const decided = new Set();
    const deadline = Date.now() + DEADLINE_MS;
    let state = null;
    while (Date.now() < deadline && state !== 'succeeded' && state !== 'failed' && state !== 'canceled') {
        for (const ap of approvals) {
            if (decided.has(ap.approval_id))
                continue;
            decided.add(ap.approval_id);
            const result = await client.decide(ap.approval_id, 'approve', ap.approval_hash);
            check(result === 'ok', `decide: approval ${ap.approval_id} accepted`);
        }
        const last = events.filter(e => e.type === 'task.state' && e.task_id === taskId).pop();
        state = last?.data?.state ?? null;
        await sleep(100);
    }
    check(state === 'succeeded', 'task: reached succeeded');
    check(decided.size >= 1, 'task: at least one approval was decided');

    // Events arrive in seq order, with no duplicates, within the session.
    const bySession = new Map();
    for (const ev of events) {
        const list = bySession.get(ev.session_id) ?? [];
        list.push(ev.seq);
        bySession.set(ev.session_id, list);
    }
    let ordered = true;
    for (const seqs of bySession.values())
        for (let i = 1; i < seqs.length; i++)
            if (seqs[i] <= seqs[i - 1])
                ordered = false;
    check(ordered && events.length > 0, 'events: seq strictly increasing per session, no duplicates');

    // A 0644 launch file is refused: the client drops to disconnected and stays there.
    writeLaunch(launchPath, {mode: 0o644});
    await waitFor('refusal of a 0644 launch file', () => !client.connected);
    await sleep(2500);
    check(!client.connected, 'refuse: a 0644 launch file keeps the client disconnected');
    check(connectedLog.at(-1).on === false && connectedLog.at(-1).sid === null,
        'refuse: onConnected(false, null) reported');

    // A fresh 0600 file (replaced with rename) reconnects with a new session.
    writeLaunch(launchPath, {extra: {rev: 2}});
    await waitFor('reconnect after the file is replaced', () => client.connected);
    const sid2 = client.sessionId;
    check(sid2 !== null && sid2 !== sid1, 'reconnect: a replaced launch file starts a new session');

    // Deleting the file means no core: onConnected(false, null).
    Gio.File.new_for_path(launchPath).delete(null);
    await waitFor('disconnect after deletion', () => !client.connected);
    check(connectedLog.at(-1).on === false, 'delete: deleting launch.json gives onConnected(false)');

    // After destroy, no callbacks arrive.
    const before = connectedLog.length + events.length;
    client.destroy();
    writeLaunch(launchPath);
    await sleep(1500);
    check(connectedLog.length + events.length === before, 'destroy: no callbacks after destroy()');
    Gio.File.new_for_path(launchPath).delete(null);
}

async function main() {
    if (!URL || !TOKEN) {
        print('Set ARTEMIS_TEST_URL and ARTEMIS_TEST_TOKEN to a running core.');
        return 2;
    }
    testParser();
    const dir = GLib.dir_make_tmp('artemis-core-test-XXXXXX');
    try {
        await testLive(dir);
    } finally {
        for (const name of ['launch.json', 'launch.json.tmp']) {
            try {
                Gio.File.new_for_path(`${dir}/${name}`).delete(null);
            } catch {
                // already gone
            }
        }
        try {
            Gio.File.new_for_path(dir).delete(null);
        } catch {
            // left behind if something failed
        }
    }
    return failures === 0 ? 0 : 1;
}

const loop = GLib.MainLoop.new(null, false);
let exitCode = 1;
GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 240, () => {
    print('FAIL  watchdog: the test took too long');
    System.exit(1);
    return GLib.SOURCE_REMOVE;
});
main().then(code => {
    exitCode = code;
    print(code === 0 ? 'PASS' : `FAILED (${failures} checks)`);
    loop.quit();
}).catch(e => {
    print(`FAIL  ${e.message}`);
    loop.quit();
});
loop.run();
System.exit(exitCode);
