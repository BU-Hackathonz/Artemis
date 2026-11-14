import {describe, expect, it} from 'vitest';
import {
  ISLAND_MOTION as M, createIslandMotion, stepIslandMotion, stillIslandFrame,
  type IslandFrame, type IslandMode, type IslandMotionInput, type Point,
} from '../src/islandMotion';

const HOME: Point = {x: 100, y: 28};
const FAR: Point = {x: 900, y: 500};

function input(mode: IslandMode, target: Point | null = null, more: Partial<IslandMotionInput> = {}): IslandMotionInput {
  return {mode, home: HOME, target, reducedMotion: false, enabled: true, ...more};
}

/** Step for `seconds` at `fps` and return every frame. */
function run(state: ReturnType<typeof createIslandMotion>, inp: IslandMotionInput, seconds: number, fps = 60): IslandFrame[] {
  const frames: IslandFrame[] = [];
  const steps = Math.round(seconds * fps);
  for (let i = 0; i < steps; i++) frames.push(stepIslandMotion(state, inp, 1 / fps));
  return frames;
}

const last = (frames: IslandFrame[]) => frames[frames.length - 1];

describe('at rest', () => {
  it('idle is a still ring at home, and says the renderer can stop', () => {
    const frame = last(run(createIslandMotion(HOME), input('idle'), 1));
    expect(frame).toMatchObject({x: 100, y: 28, morph: 0, stretch: 0, scale: 1, energy: 0, phase: 'rest', settled: true});
    expect(frame.trail).toEqual([]);
  });

  it('working with nowhere to go stays home', () => {
    const frame = last(run(createIslandMotion(HOME), input('working', null), 2));
    expect(frame).toMatchObject({x: 100, y: 28, morph: 0, settled: true});
  });

  it('listening keeps the ring at home, quietly alive, and never leaves', () => {
    const frames = run(createIslandMotion(HOME), input('listening'), 3);
    expect(frames.every(f => f.x === 100 && f.y === 28 && f.morph === 0 && f.trail.length === 0)).toBe(true);
    expect(last(frames).energy).toBeCloseTo(M.LISTEN_ENERGY, 5);
    expect(last(frames)).toMatchObject({phase: 'listening', settled: false});
  });

  it('calms down again when listening ends', () => {
    const state = createIslandMotion(HOME);
    run(state, input('listening'), 1);
    const frames = run(state, input('idle'), 4);
    expect(frames[0].settled).toBe(false);
    expect(last(frames)).toMatchObject({energy: 0, settled: true, phase: 'rest'});
  });
});

describe('leaving: the ring collapses into a droplet before it goes anywhere', () => {
  it('takes MORPH_SECONDS, eased in and out, and does not move until it is done', () => {
    const state = createIslandMotion(HOME);
    const half = last(run(state, input('working', FAR), M.MORPH_SECONDS / 2, 240));
    expect(half.morph).toBeCloseTo(0.5, 1); // ease-in-out cubic is 0.5 at the halfway point
    expect(half).toMatchObject({x: 100, y: 28, phase: 'leaving', settled: false});
    const unchanged = stepIslandMotion(createIslandMotion(HOME), input('working', FAR), 0); // no time, no change
    expect(unchanged.morph).toBe(0);
    const early = last(run(createIslandMotion(HOME), input('working', FAR), M.MORPH_SECONDS / 4, 240));
    expect(early.morph).toBeLessThan(0.1); // slow start: 4 * 0.25^3 = 0.0625

    const done = last(run(state, input('working', FAR), M.MORPH_SECONDS / 2 + 0.02, 240));
    expect(done.morph).toBe(1);
    expect(done.phase).toBe('travelling');
  });

  it('opens back into the ring if the work ends before it has left', () => {
    const state = createIslandMotion(HOME);
    run(state, input('working', FAR), 0.2);
    const frames = run(state, input('idle'), 1.5);
    expect(frames.every(f => f.x === 100 && f.y === 28)).toBe(true);
    expect(last(frames)).toMatchObject({morph: 0, settled: true});
  });
});

describe('travelling', () => {
  it('reaches the target without bouncing past it, then holds still there', () => {
    const state = createIslandMotion(HOME);
    const frames = run(state, input('working', FAR), 3);
    const end = last(frames);
    expect(end.x).toBeCloseTo(900, 0);
    expect(end.y).toBeCloseTo(500, 0);
    expect(Math.max(...frames.map(f => f.x))).toBeLessThan(900 + 12); // damping about 0.84: a hair of overshoot at most
    expect(end.stretch).toBeLessThan(0.01);
    expect(end.settled).toBe(false); // away from home: still part of a task
  });

  it('stretches along its direction of travel, never beyond 1.3x', () => {
    const frames = run(createIslandMotion(HOME), input('working', {x: 4000, y: 28}), 3, 120);
    const peak = frames.reduce((a, b) => (b.stretch > a.stretch ? b : a));
    expect(peak.stretch).toBeGreaterThan(0.25);
    expect(Math.max(...frames.map(f => f.stretch))).toBeLessThanOrEqual(M.MAX_STRETCH + 1e-9);
    expect(peak.dirX).toBeCloseTo(1, 3);
    expect(peak.dirY).toBeCloseTo(0, 3);
  });

  it('a short hop barely stretches', () => {
    const frames = run(createIslandMotion(HOME), input('working', {x: 160, y: 28}), 2, 120);
    expect(Math.max(...frames.map(f => f.stretch))).toBeLessThan(0.06);
  });

  it('follows a target that moves, such as a caret while a command types', () => {
    const state = createIslandMotion(HOME);
    run(state, input('working', {x: 400, y: 300}), 2);
    let frame!: IslandFrame;
    for (let i = 0; i < 120; i++) frame = stepIslandMotion(state, input('working', {x: 400 + i * 2, y: 300}), 1 / 60);
    expect(frame.x).toBeGreaterThan(600); // it kept up, a little behind
    expect(frame.x).toBeLessThan(640);
    expect(frame.y).toBeCloseTo(300, 0);
    expect(frame.dirX).toBeGreaterThan(0.99);
  });

  it('leaves a short thin trail: points about 6 px apart that fade within 0.45 s', () => {
    const state = createIslandMotion(HOME);
    const frames = run(state, input('working', FAR), 1.2, 120);
    const busiest = frames.reduce((a, b) => (b.trail.length > a.trail.length ? b : a));
    expect(busiest.trail.length).toBeGreaterThan(10);
    for (const frame of frames) {
      for (let i = 1; i < frame.trail.length; i++) {
        const a = frame.trail[i - 1], b = frame.trail[i];
        expect(Math.hypot(b.x - a.x, b.y - a.y)).toBeGreaterThanOrEqual(M.TRAIL_SPACING - 1e-6);
        expect(a.age).toBeGreaterThanOrEqual(b.age); // oldest first
      }
      expect(frame.trail.every(p => p.age <= M.TRAIL_LIFE + 1 / 240)).toBe(true);
    }
    // Once it has stopped at the target, the trail is gone within its lifetime.
    const settledThere = last(run(state, input('working', FAR), 2.5, 120));
    expect(settledThere.trail.length).toBeLessThanOrEqual(1);
  });
});

describe('coming home', () => {
  function awayAt(target: Point) {
    const state = createIslandMotion(HOME);
    run(state, input('working', target), 3);
    return state;
  }

  it.each<[string, IslandMode]>([['the task ends', 'idle'], ['an approval is waiting', 'awaiting_approval'], ['listening starts', 'listening']])(
    'flies back, lands with a squash and a wobble, then opens into the ring when %s', (_label, mode) => {
      const state = awayAt(FAR);
      const frames = run(state, input(mode), 6, 120);
      expect(frames[0].phase).toBe('returning');
      const landing = frames.findIndex(f => f.phase === 'landing');
      expect(landing).toBeGreaterThan(0);
      // Still a droplet when it touches down; it opens afterwards.
      expect(frames[landing].morph).toBeGreaterThan(0.95);
      expect(frames[landing]).toMatchObject({x: 100, y: 28});
      const after = frames.slice(landing);
      expect(Math.min(...after.map(f => f.scale))).toBeLessThan(0.97); // the squash
      expect(Math.max(...after.map(f => f.scale))).toBeGreaterThan(1.005); // the jelly overshoot
      expect(Math.max(...after.map(f => f.energy))).toBeGreaterThan(0.45); // the wobble
      const end = last(frames);
      expect(end).toMatchObject({x: 100, y: 28, morph: 0});
      expect(end.scale).toBeCloseTo(1, 2);
      expect(end.trail).toEqual([]);
    });

  it('holds completely still while an approval is waiting: motion never speaks for a decision', () => {
    const state = awayAt(FAR);
    run(state, input('awaiting_approval'), 8, 120);
    const frames = run(state, input('awaiting_approval'), 2);
    expect(frames.every(f => f.settled && f.phase === 'rest' && f.energy === 0 && f.scale === 1 && f.morph === 0)).toBe(true);
    // A target offered while an approval is open is ignored.
    const offered = run(state, input('awaiting_approval', FAR), 2);
    expect(offered.every(f => f.settled && f.x === 100 && f.y === 28)).toBe(true);
  });

  it('a wobble calms at ENERGY_DECAY per second', () => {
    const state = createIslandMotion(HOME);
    state.energy = 1;
    const frame = last(run(state, input('idle'), 1, 240));
    expect(frame.energy).toBeCloseTo(Math.exp(-M.ENERGY_DECAY), 3);
  });

  it('goes straight back out if work resumes on the way home', () => {
    const state = awayAt(FAR);
    run(state, input('idle'), 0.2);
    const frames = run(state, input('working', FAR), 3);
    expect(frames[0].phase).toBe('travelling');
    expect(last(frames).x).toBeCloseTo(900, 0);
  });
});

describe('when the island itself moves', () => {
  it('a resting ring is simply at the new place: no flight, no stretch, no trail', () => {
    const state = createIslandMotion(HOME);
    run(state, input('idle'), 0.5);
    const frame = stepIslandMotion(state, {...input('idle'), home: {x: 1500, y: 700}}, 1 / 60);
    expect(frame).toMatchObject({x: 1500, y: 700, stretch: 0, settled: true});
    expect(frame.trail).toEqual([]);
  });

  it('a warp while it is away starts again at the new home instead of streaking across', () => {
    const state = createIslandMotion(HOME);
    run(state, input('working', FAR), 3);
    const frame = stepIslandMotion(state, {...input('idle'), home: {x: 3000, y: 28}}, 1 / 60);
    expect(frame).toMatchObject({x: 3000, y: 28, stretch: 0, morph: 0});
    expect(frame.trail).toEqual([]);
  });
});

describe('reduced motion, and the motion setting', () => {
  it.each<[string, Partial<IslandMotionInput>]>([['prefers-reduced-motion', {reducedMotion: true}], ['motion turned off', {enabled: false}]])(
    '%s: one still frame, whatever Artemis is doing', (_label, more) => {
      for (const mode of ['idle', 'listening', 'working', 'awaiting_approval'] as IslandMode[]) {
        const state = createIslandMotion(HOME);
        const frames = run(state, input(mode, FAR, more), 2);
        for (const frame of frames) {
          expect(frame).toEqual(stillIslandFrame(HOME, mode));
          expect(frame).toMatchObject({x: 100, y: 28, morph: 0, stretch: 0, scale: 1, energy: 0, settled: true});
        }
      }
    });

  it('turning it on mid-flight stops at once, and turning it back on starts from rest', () => {
    const state = createIslandMotion(HOME);
    run(state, input('working', FAR), 1);
    const off = stepIslandMotion(state, input('working', FAR, {reducedMotion: true}), 1 / 60);
    expect(off).toEqual(stillIslandFrame(HOME, 'working'));
    const on = stepIslandMotion(state, input('working', FAR), 1 / 60);
    expect(on).toMatchObject({x: 100, y: 28, phase: 'leaving'});
    expect(on.morph).toBeLessThan(0.01);
  });
});

describe('robustness', () => {
  it('gives the same motion at 60 and 144 frames per second', () => {
    const at = (fps: number) => {
      const state = createIslandMotion(HOME);
      run(state, input('working', FAR), 1.5, fps);
      return last(run(state, input('idle'), 0.5, fps));
    };
    const slow = at(60), fast = at(144);
    expect(fast.x).toBeCloseTo(slow.x, 0);
    expect(fast.y).toBeCloseTo(slow.y, 0);
    expect(fast.stretch).toBeCloseTo(slow.stretch, 2);
    expect(fast.morph).toBeCloseTo(slow.morph, 2);
  });

  it('a stalled frame is clamped, and odd time steps do no harm', () => {
    const state = createIslandMotion(HOME);
    run(state, input('working', FAR), 1);
    const before = stepIslandMotion(state, input('working', FAR), 0);
    const after = stepIslandMotion(state, input('working', FAR), 5); // a five-second stall
    expect(Math.hypot(after.x - before.x, after.y - before.y)).toBeLessThan(2500 * M.MAX_DT + 1);
    for (const dt of [-1, Number.NaN, 0]) {
      const frame = stepIslandMotion(state, input('working', FAR), dt);
      expect(Number.isFinite(frame.x) && Number.isFinite(frame.y) && Number.isFinite(frame.scale)).toBe(true);
    }
  });

  it('the returned trail is a copy: a renderer cannot disturb the model', () => {
    const state = createIslandMotion(HOME);
    const frame = last(run(state, input('working', FAR), 1));
    (frame.trail as unknown as {x: number}[])[0].x = -999;
    expect(state.trail[0].x).not.toBe(-999);
  });
});
