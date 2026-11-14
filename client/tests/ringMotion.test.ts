import {describe, expect, it} from 'vitest';
import {RING_STATES, staticRingTarget} from '../src/ringRenderer';

describe('reduced motion: the ring is one still frame', () => {
  it('drops every movement term but keeps glow and dim for each state', () => {
    for (const state of Object.values(RING_STATES)) {
      const still = staticRingTarget(state);
      expect({morph: still.morph, spin: still.spin, wobble: still.wobble, orbit: still.orbit, level: still.level})
        .toEqual({morph: 0, spin: 0, wobble: 0, orbit: 0, level: 0});
      expect(still.glow).toBe(state.glow);
      expect(still.dim).toBe(state.dim);
    }
  });
  it('still tells failed apart from ready by colour, and attention apart from ready by brightness', () => {
    expect(staticRingTarget(RING_STATES.failed).dim).not.toBe(staticRingTarget(RING_STATES.ready).dim);
    expect(staticRingTarget(RING_STATES.attention).glow).toBeGreaterThan(staticRingTarget(RING_STATES.ready).glow);
  });
});
