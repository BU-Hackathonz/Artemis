import {describe, expect, it} from 'vitest';
import {ringTarget} from '../src/LiquidRing';
import {RING_STATES} from '../src/ringRenderer';

const base = {connected: true, taskState: 'idle' as const, microphone: 'idle' as const, approval: false};

describe('ringTarget: the ring pictures the state the text announces', () => {
  it('is dim and still until the core is connected', () => {
    expect(ringTarget({...base, connected: false, taskState: 'working'})).toBe(RING_STATES.failed);
  });
  it('rests when idle or canceled', () => {
    expect(ringTarget(base)).toBe(RING_STATES.ready);
    expect(ringTarget({...base, taskState: 'canceled'})).toBe(RING_STATES.ready);
  });
  it('listening wins over the task, and needing a decision wins over working', () => {
    expect(ringTarget({...base, taskState: 'working', microphone: 'listening'})).toBe(RING_STATES.listening);
    expect(ringTarget({...base, taskState: 'working', approval: true})).toBe(RING_STATES.attention);
    expect(ringTarget({...base, taskState: 'awaiting_reply'})).toBe(RING_STATES.attention);
  });
  it('maps the task states', () => {
    expect(ringTarget({...base, taskState: 'queued'})).toBe(RING_STATES.working);
    expect(ringTarget({...base, taskState: 'working'})).toBe(RING_STATES.working);
    expect(ringTarget({...base, taskState: 'running'})).toBe(RING_STATES.running);
    expect(ringTarget({...base, taskState: 'succeeded'})).toBe(RING_STATES.done);
    expect(ringTarget({...base, taskState: 'failed'})).toBe(RING_STATES.failed);
  });
});
