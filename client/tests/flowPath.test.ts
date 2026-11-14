import {describe, expect, it} from 'vitest';
import {chooseTarget, flowMode, wanderTarget} from '../src/flowPath';

describe('wanderTarget', () => {
  it('stays inside the window, away from the edges, for a long time', () => {
    for (let t = 0; t < 600; t += 0.37) {
      const p = wanderTarget(t, 1480, 864);
      expect(p.x).toBeGreaterThan(1480 * 0.1);
      expect(p.x).toBeLessThan(1480 * 0.9);
      expect(p.y).toBeGreaterThan(864 * 0.1);
      expect(p.y).toBeLessThan(864 * 0.8);
    }
  });

  it('moves, so the droplet has somewhere to go', () => {
    const a = wanderTarget(0, 1000, 800);
    const b = wanderTarget(3, 1000, 800);
    expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThan(50);
  });

  it('scales with the window', () => {
    const small = wanderTarget(1, 500, 400);
    const big = wanderTarget(1, 1000, 800);
    expect(big.x).toBeCloseTo(small.x * 2);
    expect(big.y).toBeCloseTo(small.y * 2);
  });
});

describe('flowMode', () => {
  it('works while busy', () => expect(flowMode({approval: false, busy: true})).toBe('working'));
  it('is idle when nothing happens', () => expect(flowMode({approval: false, busy: false})).toBe('idle'));
  it('holds still for an approval, even while busy', () => {
    expect(flowMode({approval: true, busy: true})).toBe('awaiting_approval');
    expect(flowMode({approval: true, busy: false})).toBe('awaiting_approval');
  });
});

describe('chooseTarget', () => {
  it('follows a caret that is on this screen', () => {
    expect(chooseTarget({x: 300, y: 200}, 5, 1480, 864)).toEqual({x: 300, y: 200});
  });
  it('falls back to the slow path with no caret', () => {
    expect(chooseTarget(null, 5, 1480, 864)).toEqual(wanderTarget(5, 1480, 864));
  });
  it('ignores a caret outside the window or a nonsense one', () => {
    for (const bad of [{x: -5, y: 10}, {x: 99999, y: 10}, {x: 10, y: -1}, {x: 10, y: 99999}, {x: NaN, y: 3}]) {
      expect(chooseTarget(bad, 2, 1480, 864)).toEqual(wanderTarget(2, 1480, 864));
    }
  });
});
