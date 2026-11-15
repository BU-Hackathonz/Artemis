// What the island shows, worked out only from the core's events (contracts.md §8).
//
// Pure module: no GNOME imports, no timers, no clock. island.js renders what view() returns, and
// client/tests/islandState.test.js replays recorded event sequences through reduce().
//
// The eight island states: idle, listening, thinking, editing, running, speaking, done, error.
//   - speaking shows only when no task state holds; over a task the task's state stays shown
//     (Artemis talks through most of a task), with view().speaking true. Listening and
//     transcribing win over both, because push-to-talk stops speech (§5.1).
//   - awaiting_approval is not a state of its own: the task keeps its state, marked as waiting.
//   - done goes back to idle after about 3 s; that delay is the client's (DONE_MS), the only timer.

export const STATES = ['idle', 'listening', 'thinking', 'editing', 'running', 'speaking', 'done', 'error'];
export const DONE_MS = 3000;

const LABELS = {
    idle: 'Ready',
    listening: 'Listening',
    thinking: 'Thinking',
    editing: 'Editing files',
    running: 'Running the program',
    speaking: 'Speaking',
    done: 'Done',
    error: 'Something went wrong',
};

const DESCRIPTIONS = {
    idle: 'Artemis is ready for a request.',
    listening: 'Artemis is listening. Release the shortcut when you finish speaking.',
    thinking: 'Artemis is working out what to do.',
    editing: 'Artemis is changing files in your project.',
    running: 'Artemis is running your program.',
    speaking: 'Artemis is speaking.',
    done: 'Artemis finished the request.',
    error: 'The last request failed. Artemis said why.',
};

/** The state before any event, and while no core is connected. */
export function initial(connected = false) {
    return {
        connected,
        voice: 'idle',       // voice.activity: idle | listening | transcribing | speaking
        taskId: null,        // the task the island follows (the newest one)
        phase: 'idle',       // idle | thinking | editing | running | done | error
        waiting: false,      // an approval is open for this task
        approvalId: null,
        changed: false,      // a file.changed arrived in this task
        ran: false,          // a run.result arrived in this task
        runFailed: false,    // ... with a non-zero exit code, or it timed out
        lastSeq: 0,
    };
}

const ENDED = new Set(['succeeded', 'failed', 'canceled']);

/**
 * Apply one event (the contracts.md §2 envelope) and return the next state. Never mutates.
 * Events with a seq already seen are dropped (the core never repeats one; a replay after a
 * reconnect can).
 */
export function reduce(state, event) {
    if (!event || typeof event.type !== 'string') return state;
    if (typeof event.seq === 'number') {
        if (event.seq <= state.lastSeq) return state;
        state = {...state, lastSeq: event.seq};
    } else {
        state = {...state};
    }
    const data = event.data ?? {};
    const tid = event.task_id ?? null;
    // A newer task replaces the one the island follows: done and error end with the next request.
    const forTask = () => {
        if (tid && tid !== state.taskId)
            Object.assign(state, {taskId: tid, phase: 'thinking', waiting: false, approvalId: null, changed: false, ran: false, runFailed: false});
    };

    switch (event.type) {
    case 'voice.activity':
        if (['idle', 'listening', 'transcribing', 'speaking'].includes(data.state)) state.voice = data.state;
        return state;

    case 'task.state': {
        const s = data.state;
        if (!tid) return state;
        if (tid !== state.taskId && ENDED.has(s)) return state; // an older task ending: not ours
        forTask();
        if (s === 'queued' || s === 'working') {
            state.waiting = false;
            state.approvalId = null;
            state.phase = state.changed && !state.ran ? 'editing' : 'thinking';
        } else if (s === 'awaiting_approval') {
            state.waiting = true;
        } else if (s === 'running') {
            state.waiting = false;
            state.approvalId = null;
            state.phase = 'running';
        } else if (s === 'succeeded') {
            Object.assign(state, {waiting: false, approvalId: null, phase: state.runFailed ? 'error' : 'done'});
        } else if (s === 'failed') {
            Object.assign(state, {waiting: false, approvalId: null, phase: 'error'});
        } else if (s === 'canceled') {
            Object.assign(state, {waiting: false, approvalId: null, phase: 'idle'});
        }
        return state;
    }

    case 'approval.requested':
        if (!tid) return state;
        forTask();
        state.waiting = true;
        state.approvalId = data.approval_id ?? null;
        if (data.kind === 'write_files') state.phase = 'editing';
        return state;

    case 'approval.resolved':
        if (data.approval_id && data.approval_id === state.approvalId) {
            state.waiting = false;
            state.approvalId = null;
        }
        return state;

    case 'file.changed':
        if (!tid) return state;
        forTask();
        state.changed = true;
        state.phase = 'editing';
        return state;

    case 'run.result': {
        if (!tid) return state;
        forTask();
        state.ran = true;
        const failed = data.timed_out === true || (data.exit_code !== 0 && data.exit_code !== undefined);
        if (failed) state.runFailed = true;
        state.phase = failed ? 'error' : 'thinking'; // the agent answers next
        return state;
    }

    case 'error':
        // A task_active refusal while a task works is spoken, not a failure of that task.
        if (data.code === 'task_active') return state;
        state.phase = 'error';
        state.waiting = false;
        return state;

    default:
        return state;
    }
}

/**
 * The stream dropped (false) or came back (true). The task view is kept: a reconnect to the same
 * session replays only what was missed. A new session (a restarted core) starts from initial(true).
 */
export function setConnected(state, connected) {
    if (state.connected === connected) return state;
    return {...state, connected};
}

/** Done shown long enough (DONE_MS): back to idle, unless something newer is showing. */
export function expireDone(state) {
    return state.phase === 'done' ? {...state, phase: 'idle'} : state;
}

/**
 * What to show: {state, base, waiting, speaking, label, accessibleName, description}.
 * `state` is one of STATES; `base` is the task's own state; `speaking` is true while Artemis talks.
 */
export function view(state) {
    if (!state.connected) {
        return {
            state: 'idle', base: 'idle', waiting: false, connected: false,
            label: 'Core not running',
            accessibleName: 'Artemis core is not running',
            description: 'Start Artemis with infra/start.sh, then the island connects by itself.',
        };
    }
    const base = state.phase;
    let shown = base;
    // Artemis talks through most of a task, so speaking only shows on its own when no task state
    // holds (idle). Over a task, the task's state stays in the name and label (Editing files,
    // Running the program, Done), and `speaking` says that Artemis is also talking.
    if (state.voice === 'listening') shown = 'listening';
    else if (state.voice === 'transcribing') shown = 'thinking';
    else if (state.voice === 'speaking' && base === 'idle') shown = 'speaking';
    const speaking = state.voice === 'speaking';
    const waiting = state.waiting && shown !== 'listening';
    let label = LABELS[shown];
    if (state.voice === 'transcribing') label = 'Transcribing';
    if (waiting) label += ', waiting for your approval';
    const description = waiting ? 'Artemis is waiting for you to approve or reject a change.' : DESCRIPTIONS[shown];
    return {state: shown, base, waiting, speaking, connected: true, label, accessibleName: `Artemis: ${label}`, description};
}
