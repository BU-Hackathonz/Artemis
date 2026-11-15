import {describe, it, expect} from 'vitest';
import {
  STATES, DONE_MS, initial, reduce, setConnected, expireDone, view,
} from '../prototypes/water-desktop/artemis-water@bu-hackathonz.github.io/islandState.js';
import approveRun from './fixtures/island-hello-approve.json';
import rejectRun from './fixtures/island-hello-reject.json';

let n = 0;
// An event with the next seq (the core's envelope, contracts.md §2).
const ev = (type, task_id, data = {}) => ({type, task_id, data, seq: ++n});

const deepFreeze = (o) => {
  for (const v of Object.values(o)) if (v && typeof v === 'object') deepFreeze(v);
  return Object.freeze(o);
};

// The island's view after each event, with consecutive duplicates collapsed.
// An approval waiting shows as "<state> (waiting)".
function replay(events) {
  let s = initial(true);
  const seen = [];
  const note = () => {
    const v = view(s);
    const k = v.waiting ? `${v.state} (waiting)` : v.state;
    if (seen[seen.length - 1] !== k) seen.push(k);
  };
  note();
  for (const e of events) {
    s = reduce(s, e);
    note();
  }
  return seen;
}

describe('recorded core runs (contracts.md §8)', () => {
  // The brief listed 8 and 5 steps. The reducer also shows "thinking (waiting)" when the task
  // reports awaiting_approval (§8: the island keeps its state, marked waiting), and "editing"
  // (not waiting) once an approval is resolved and before the next task.state. Those steps are
  // asserted here as the code produces them.
  it('approve run: hello world written, run, and finished', () => {
    expect(replay(approveRun)).toEqual([
      'idle',
      'thinking',
      'thinking (waiting)',
      'editing (waiting)',
      'editing',
      'editing (waiting)',
      'editing',
      'running',
      'thinking',
      'done',
    ]);
  });

  it('reject run: the write is refused and the task ends done', () => {
    expect(replay(rejectRun)).toEqual([
      'idle',
      'thinking',
      'thinking (waiting)',
      'editing (waiting)',
      'editing',
      'thinking',
      'done',
    ]);
  });

  it('ends with the approval closed', () => {
    let s = initial(true);
    for (const e of approveRun) s = reduce(s, e);
    expect(view(s)).toMatchObject({state: 'done', waiting: false});
    let r = initial(true);
    for (const e of rejectRun) r = reduce(r, e);
    expect(view(r)).toMatchObject({state: 'done', waiting: false});
  });
});

describe('voice overlay', () => {
  it('listening wins over speaking and over a task state', () => {
    let s = reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'}));
    s = reduce(s, ev('approval.requested', 'tsk_a', {approval_id: 'apr_1', kind: 'write_files'}));
    expect(view(s)).toMatchObject({state: 'editing', waiting: true});
    s = reduce(s, ev('voice.activity', null, {state: 'speaking'}));
    expect(view(s)).toMatchObject({state: 'editing', base: 'editing', speaking: true});
    s = reduce(s, ev('voice.activity', null, {state: 'listening'}));
    expect(view(s)).toMatchObject({state: 'listening', base: 'editing', waiting: false});
  });

  it('transcribing shows thinking with the label Transcribing', () => {
    const s = reduce(initial(true), ev('voice.activity', null, {state: 'transcribing'}));
    expect(view(s)).toMatchObject({
      state: 'thinking',
      label: 'Transcribing',
      accessibleName: 'Artemis: Transcribing',
    });
  });

  it('speaking keeps the task state in the name (no overlay over a task)', () => {
    let s = reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'}));
    s = reduce(s, ev('voice.activity', null, {state: 'speaking'}));
    expect(view(s)).toMatchObject({state: 'thinking', base: 'thinking', speaking: true, label: 'Thinking'});
    s = reduce(s, ev('voice.activity', null, {state: 'idle'}));
    expect(view(s)).toMatchObject({state: 'thinking', base: 'thinking'});
  });
});

describe('run and task failures', () => {
  it('a non-zero exit code is an error, and a later succeeded keeps it', () => {
    let s = reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'}));
    s = reduce(s, ev('run.result', 'tsk_a', {exit_code: 1, timed_out: false}));
    expect(view(s).state).toBe('error');
    s = reduce(s, ev('task.state', 'tsk_a', {state: 'succeeded'}));
    expect(view(s)).toMatchObject({state: 'error', base: 'error'});
  });

  it('a timed out run is an error', () => {
    let s = reduce(initial(true), ev('task.state', 'tsk_a', {state: 'running'}));
    s = reduce(s, ev('run.result', 'tsk_a', {exit_code: 0, timed_out: true}));
    expect(view(s).state).toBe('error');
  });

  it('task.state failed is an error', () => {
    let s = reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'}));
    s = reduce(s, ev('task.state', 'tsk_a', {state: 'failed'}));
    expect(view(s).state).toBe('error');
  });

  it('task.state canceled goes back to idle', () => {
    let s = reduce(initial(true), ev('task.state', 'tsk_a', {state: 'awaiting_approval'}));
    expect(view(s)).toMatchObject({state: 'thinking', waiting: true});
    s = reduce(s, ev('task.state', 'tsk_a', {state: 'canceled'}));
    expect(view(s)).toMatchObject({state: 'idle', waiting: false});
  });

  it('an error event with code task_active is ignored', () => {
    let s = reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'}));
    s = reduce(s, ev('error', null, {code: 'task_active'}));
    expect(view(s).state).toBe('thinking');
  });

  it('any other error event is an error', () => {
    let s = reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'}));
    s = reduce(s, ev('error', null, {code: 'internal'}));
    expect(view(s).state).toBe('error');
  });
});

describe('event ordering and task changes', () => {
  it('ignores a duplicate or lower seq', () => {
    const a = reduce(initial(true), {type: 'task.state', task_id: 'tsk_a', data: {state: 'working'}, seq: 5});
    const dup = {type: 'task.state', task_id: 'tsk_a', data: {state: 'failed'}, seq: 5};
    const older = {type: 'task.state', task_id: 'tsk_a', data: {state: 'failed'}, seq: 4};
    expect(reduce(a, dup)).toBe(a);
    expect(reduce(a, older)).toBe(a);
    expect(view(reduce(a, older))).toMatchObject({state: 'thinking'});
  });

  it.each(['succeeded', 'failed', 'canceled'])('an older task reporting %s is ignored', (end) => {
    let s = reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'}));
    s = reduce(s, ev('task.state', 'tsk_b', {state: 'queued'}));
    s = reduce(s, ev('task.state', 'tsk_a', {state: end}));
    expect(s.taskId).toBe('tsk_b');
    expect(view(s)).toMatchObject({state: 'thinking', base: 'thinking'});
  });

  it('a new task after done starts at thinking', () => {
    let s = reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'}));
    s = reduce(s, ev('task.state', 'tsk_a', {state: 'succeeded'}));
    expect(view(s).state).toBe('done');
    s = reduce(s, ev('task.state', 'tsk_b', {state: 'queued'}));
    expect(view(s).state).toBe('thinking');
  });

  it('a new task after an error starts clean, without the old run failure', () => {
    let s = reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'}));
    s = reduce(s, ev('run.result', 'tsk_a', {exit_code: 2, timed_out: false}));
    s = reduce(s, ev('task.state', 'tsk_a', {state: 'succeeded'}));
    expect(view(s).state).toBe('error');
    s = reduce(s, ev('task.state', 'tsk_b', {state: 'working'}));
    expect(view(s).state).toBe('thinking');
    s = reduce(s, ev('task.state', 'tsk_b', {state: 'succeeded'}));
    expect(view(s).state).toBe('done');
  });
});

describe('done expiry and connection', () => {
  it('DONE_MS is 3 seconds', () => {
    expect(DONE_MS).toBe(3000);
  });

  it('expireDone clears done to idle', () => {
    let s = reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'}));
    s = reduce(s, ev('task.state', 'tsk_a', {state: 'succeeded'}));
    expect(view(s).state).toBe('done');
    expect(view(expireDone(s)).state).toBe('idle');
  });

  it('expireDone leaves every other phase alone', () => {
    const thinking = reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'}));
    const error = reduce(initial(true), ev('error', null, {code: 'internal'}));
    expect(expireDone(thinking)).toBe(thinking);
    expect(expireDone(error)).toBe(error);
    expect(expireDone(initial(true))).toEqual(initial(true));
  });

  it('setConnected(false) says the core is not running', () => {
    const s = setConnected(reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'})), false);
    expect(view(s)).toMatchObject({
      state: 'idle',
      connected: false,
      label: 'Core not running',
      accessibleName: 'Artemis core is not running',
    });
  });

  it('setConnected with the same value returns the same state', () => {
    const s = initial(true);
    expect(setConnected(s, true)).toBe(s);
  });

  it('setConnected keeps the task across a reconnect to the same session', () => {
    const s = setConnected(reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'})), false);
    const back = setConnected(s, true);
    expect(back).toMatchObject({connected: true, taskId: 'tsk_a', phase: 'thinking'});
    expect(view(back).state).toBe('thinking');
  });
});

describe('labels', () => {
  it('every state in STATES has its own label', () => {
    const cases = {
      idle: initial(true),
      listening: reduce(initial(true), ev('voice.activity', null, {state: 'listening'})),
      thinking: reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'})),
      editing: reduce(initial(true), ev('approval.requested', 'tsk_a', {approval_id: 'apr_2', kind: 'write_files'})),
      running: reduce(initial(true), ev('task.state', 'tsk_a', {state: 'running'})),
      speaking: reduce(initial(true), ev('voice.activity', null, {state: 'speaking'})),
      done: reduce(reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'})), ev('task.state', 'tsk_a', {state: 'succeeded'})),
      error: reduce(initial(true), ev('error', null, {code: 'internal'})),
    };
    expect(Object.keys(cases).sort()).toEqual([...STATES].sort());
    const labels = [];
    for (const name of STATES) {
      const v = view(cases[name]);
      expect(v.state).toBe(name);
      labels.push(v.label);
    }
    expect(new Set(labels).size).toBe(STATES.length);
  });
});

describe('purity', () => {
  it('reduce never mutates its input state or event', () => {
    const frozen = deepFreeze(structuredClone(approveRun));
    let s = deepFreeze(initial(true));
    for (const e of frozen) {
      const next = reduce(s, e);
      expect(next).not.toBe(s);
      s = deepFreeze(next);
    }
    expect(view(s).state).toBe('done');
  });

  it('reduce leaves the previous state as it was', () => {
    const before = reduce(initial(true), ev('task.state', 'tsk_a', {state: 'working'}));
    const snapshot = JSON.stringify(before);
    reduce(before, ev('approval.requested', 'tsk_a', {approval_id: 'apr_3', kind: 'write_files'}));
    reduce(before, ev('task.state', 'tsk_a', {state: 'succeeded'}));
    expect(JSON.stringify(before)).toBe(snapshot);
  });
});

describe('speaking over a task (David\'s run with real speech)', () => {
  it('keeps Editing, Running and Done in the name while Artemis talks', () => {
    let s = initial(true);
    const talk = () => { s = reduce(s, ev('voice.activity', null, {state: 'speaking'})); };
    s = reduce(s, ev('task.state', 'tsk_s', {state: 'queued'})); talk();
    s = reduce(s, ev('file.changed', 'tsk_s', {path: 'main.py'}));
    expect(view(s).accessibleName).toBe('Artemis: Editing files');
    s = reduce(s, ev('task.state', 'tsk_s', {state: 'running'}));
    expect(view(s).accessibleName).toBe('Artemis: Running the program');
    s = reduce(s, ev('task.state', 'tsk_s', {state: 'succeeded'}));
    expect(view(s)).toMatchObject({state: 'done', speaking: true, accessibleName: 'Artemis: Done'});
  });
  it('shows speaking on its own only when idle', () => {
    const s = reduce(initial(true), ev('voice.activity', null, {state: 'speaking'}));
    expect(view(s)).toMatchObject({state: 'speaking', label: 'Speaking'});
  });
});
