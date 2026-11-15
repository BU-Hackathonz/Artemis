// End-to-end check without GNOME Shell: core.js + islandState.js against a running core.
// Submits the stub Hello World request, approves (or rejects) each approval, and prints the island
// states it went through. Usage (a core started by infra/start.sh, or by hand):
//   gjs -m client/prototypes/water-desktop/tests/island-states-gjs.js [approve|reject] [launch.json]
// Exit 0 when the states are the expected ones.
import GLib from 'gi://GLib';
import {CoreClient, defaultLaunchPath} from '../artemis-water@bu-hackathonz.github.io/core.js';
import {initial, reduce, setConnected, view} from '../artemis-water@bu-hackathonz.github.io/islandState.js';

const [decision = 'approve', launchPath = defaultLaunchPath()] = ARGV;
const want = decision === 'approve'
    ? ['idle', 'thinking', 'thinking (waiting)', 'editing (waiting)', 'editing', 'editing (waiting)', 'editing', 'running', 'thinking', 'done']
    : ['idle', 'thinking', 'thinking (waiting)', 'editing (waiting)', 'editing', 'thinking', 'done'];
const loop = new GLib.MainLoop(null, false);
let state = initial(false);
const seen = [];
const note = () => {
    const v = view(state);
    const s = v.connected ? `${v.state}${v.waiting ? ' (waiting)' : ''}` : 'offline';
    if (s !== 'offline' && seen.at(-1) !== s) { seen.push(s); print(`  ${s}  (${v.accessibleName})`); }
};
let started = false;
const core = new CoreClient({
    launchPath,
    onConnected: (on) => {
        state = setConnected(on ? initial(true) : state, on);
        note();
        if (on && !started) {
            started = true;
            core.submitTask('create a Python program that prints Hello World').then((r) => { if (!r.ok) { print(`submit failed: ${r.reason}`); loop.quit(); } });
        }
    },
    onEvent: (e) => {
        state = reduce(state, e);
        note();
        if (e.type === 'approval.requested') core.decide(e.data.approval_id, decision, e.data.approval_hash);
        if (e.type === 'task.state' && ['succeeded', 'failed', 'canceled'].includes(e.data.state)) loop.quit();
    },
});
core.start();
GLib.timeout_add(GLib.PRIORITY_DEFAULT, 30000, () => { print('timed out'); loop.quit(); return GLib.SOURCE_REMOVE; });
loop.run();
core.destroy();
const ok = JSON.stringify(seen) === JSON.stringify(want);
print(ok ? 'PASS' : `FAIL: expected ${want.join(' → ')}`);
imports.system.exit(ok ? 0 : 1);
