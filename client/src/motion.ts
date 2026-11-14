// The island's movement, as numbers only: where the droplet is, how far the ring has collapsed
// into it, how stretched and wobbly it is, and the short trail behind it.
//
// Pure: no window, canvas, timer, React or Tauri in here. A renderer calls stepIslandMotion() once
// per frame with what Artemis is doing and draws the frame it gets back. Nothing imports this yet.
//
// It is decoration. It never takes focus or clicks, and it must never be the only way a state is
// told: every state also has its spoken line and its text. In particular it says nothing about an
// approval. While one is waiting the droplet comes home and holds still.
//
// The constants are the ones tuned in the GNOME prototype (client/prototypes/water-desktop,
// extension.js), so the Windows island moves the same way.

export type IslandMode = 'idle' | 'listening' | 'working' | 'awaiting_approval';
export type Point = {x: number; y: number};

export const ISLAND_MOTION = {
  MORPH_SECONDS: 0.55, // ring <-> droplet; the droplet leaves only once the ring has collapsed
  FOLLOW_STIFFNESS: 320, // spring pulling the droplet after its target (1/s^2)
  FOLLOW_DAMPING: 30, // damping ratio about 0.84: it arrives without bouncing past
  MAX_STRETCH: 0.3, // longest it gets along a fast move (1.3x)
  STRETCH_SPEED: 2500, // px/s for full stretch
  STRETCH_EASE: 12, // 1/s: how quickly the stretch follows the speed
  POP_STIFFNESS: 180, // size spring (1/s^2) ...
  POP_DAMPING: 12, // ... damping ratio about 0.45: a jelly-like overshoot
  ENERGY_DECAY: 1.4, // 1/s: how fast a wobble calms down
  SPEED_ENERGY: 0.45, // the most wobble that speed alone adds
  LISTEN_ENERGY: 0.2, // a quiet, steady liveliness while the microphone is open
  LAND_ENERGY: 0.5, // landing: a wobble ...
  LAND_POP: -1.2, // ... and a squash (size velocity, 1/s)
  MAX_ENERGY: 1.2,
  TRAIL_LIFE: 0.45, // s a bit of trail lasts
  TRAIL_SPACING: 6, // px between trail points
  MAX_JUMP: 400, // px: home moving this far in one step is a warp (dragged, new monitor), not a move
  SUBSTEP: 1 / 240, // s: fixed physics step, so the result does not depend on the frame rate
  MAX_DT: 0.05, // s: a stalled frame never turns into a lurch
  ARRIVE_DISTANCE: 0.5, // px ...
  ARRIVE_SPEED: 1, // ... and px/s under which it counts as back home
} as const;

export interface IslandMotionInput {
  mode: IslandMode;
  /** Where the ring rests (the island), in the renderer's own pixels. */
  home: Point;
  /** Where the droplet should be while working, e.g. the text caret. None: it stays home. */
  target?: Point | null;
  /** prefers-reduced-motion. */
  reducedMotion: boolean;
  /** The user's motion setting. */
  enabled: boolean;
}

/** What the renderer is being asked to draw. Descriptive only: never read state from it. */
export type IslandPhase = 'rest' | 'listening' | 'leaving' | 'travelling' | 'returning' | 'landing';

export interface IslandFrame {
  x: number;
  y: number;
  /** Unit vector of travel; the droplet stretches along it. */
  dirX: number;
  dirY: number;
  /** 0 .. MAX_STRETCH. Drawn length is (1 + stretch) along the direction of travel. */
  stretch: number;
  /** Size, 1 at rest. Dips below 1 for the squash on landing, then overshoots and settles. */
  scale: number;
  /** Wobble, 0 = calm. */
  energy: number;
  /** 0 = ring, 1 = droplet, already eased. */
  morph: number;
  /** Trail points, oldest first. age runs from 0 to TRAIL_LIFE seconds. */
  trail: readonly {x: number; y: number; age: number}[];
  phase: IslandPhase;
  /** Nothing is moving and nothing will: the renderer can stop its animation loop. */
  settled: boolean;
}

export interface IslandMotionState {
  x: number;
  y: number;
  vx: number;
  vy: number;
  dirX: number;
  dirY: number;
  stretch: number;
  scale: number;
  scaleVelocity: number;
  energy: number;
  /** Linear progress of the ring <-> droplet change, 0 .. 1. */
  morph: number;
  /** True from the moment it leaves until it has landed again. */
  away: boolean;
  homeX: number;
  homeY: number;
  trail: {x: number; y: number; age: number}[];
}

const easeInOutCubic = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export function createIslandMotion(home: Point): IslandMotionState {
  return {
    x: home.x, y: home.y, vx: 0, vy: 0, dirX: 1, dirY: 0, stretch: 0, scale: 1, scaleVelocity: 0,
    energy: 0, morph: 0, away: false, homeX: home.x, homeY: home.y, trail: [],
  };
}

/** The one still frame used under reduced motion, or with motion turned off. */
export function stillIslandFrame(home: Point, mode: IslandMode = 'idle'): IslandFrame {
  return {
    x: home.x, y: home.y, dirX: 1, dirY: 0, stretch: 0, scale: 1, energy: 0, morph: 0, trail: [],
    phase: mode === 'listening' ? 'listening' : 'rest', settled: true,
  };
}

function rest(state: IslandMotionState, home: Point): void {
  Object.assign(state, createIslandMotion(home));
}

/**
 * Advance by dt seconds and return what to draw. Mutates `state`.
 *
 * - idle: the ring rests at home.
 * - listening: the ring stays home, quietly alive.
 * - working with a target: the ring collapses into a droplet (MORPH_SECONDS), which then flies to
 *   the target and follows it, stretched by its speed and leaving a short trail.
 * - when that ends for any reason (done, canceled, an approval is waiting, no target): the droplet
 *   flies home, lands with a squash and a wobble, and opens back into the ring.
 * - reduced motion, or motion off: the still frame, always.
 */
export function stepIslandMotion(state: IslandMotionState, input: IslandMotionInput, dt: number): IslandFrame {
  const M = ISLAND_MOTION;
  const {home, mode} = input;
  if (!input.enabled || input.reducedMotion) {
    rest(state, home);
    return stillIslandFrame(home, mode);
  }

  // Home itself moved (the island was dragged, or the monitor changed).
  const homeShift = Math.hypot(home.x - state.homeX, home.y - state.homeY);
  if (!state.away) {
    // Resting: the ring is simply there. No flight, no stretch.
    state.x = home.x; state.y = home.y; state.vx = 0; state.vy = 0;
  } else if (homeShift > M.MAX_JUMP) {
    rest(state, home); // a warp while away: start again at the new home instead of streaking across
  }
  state.homeX = home.x; state.homeY = home.y;

  const target = mode === 'working' && input.target ? input.target : null;
  let remaining = Math.min(Math.max(dt, 0), M.MAX_DT);
  let landed = false;

  while (remaining > 1e-9) {
    const h = Math.min(M.SUBSTEP, remaining);
    remaining -= h;

    // The ring collapses before the droplet leaves, and opens again only once it is home.
    const wantDroplet = target !== null || state.away;
    if (wantDroplet) state.morph = Math.min(1, state.morph + h / M.MORPH_SECONDS);
    else state.morph = Math.max(0, state.morph - h / M.MORPH_SECONDS);
    if (target !== null && state.morph >= 1) state.away = true;

    if (state.away) {
      const destination = target ?? home;
      state.vx += (M.FOLLOW_STIFFNESS * (destination.x - state.x) - M.FOLLOW_DAMPING * state.vx) * h;
      state.vy += (M.FOLLOW_STIFFNESS * (destination.y - state.y) - M.FOLLOW_DAMPING * state.vy) * h;
      state.x += state.vx * h;
      state.y += state.vy * h;

      const last = state.trail[state.trail.length - 1];
      if (!last || Math.hypot(state.x - last.x, state.y - last.y) >= M.TRAIL_SPACING) {
        state.trail.push({x: state.x, y: state.y, age: 0});
      }

      if (target === null) {
        const distance = Math.hypot(home.x - state.x, home.y - state.y);
        if (distance < M.ARRIVE_DISTANCE && Math.hypot(state.vx, state.vy) < M.ARRIVE_SPEED) {
          // Home: set down with a squash and a wobble.
          state.away = false;
          state.x = home.x; state.y = home.y; state.vx = 0; state.vy = 0;
          state.energy = Math.min(state.energy + M.LAND_ENERGY, M.MAX_ENERGY);
          state.scaleVelocity += M.LAND_POP;
          landed = true;
        }
      }
    }

    const speed = Math.hypot(state.vx, state.vy);
    if (speed > 20) { state.dirX = state.vx / speed; state.dirY = state.vy / speed; }
    const stretchTo = Math.min(speed / M.STRETCH_SPEED, 1) * M.MAX_STRETCH;
    state.stretch += (stretchTo - state.stretch) * Math.min(1, h * M.STRETCH_EASE);

    state.scaleVelocity += (M.POP_STIFFNESS * (1 - state.scale) - M.POP_DAMPING * state.scaleVelocity) * h;
    state.scale += state.scaleVelocity * h;

    // Moving fast churns it; otherwise any wobble calms down. Listening keeps it quietly alive.
    const floor = Math.max(Math.min(speed / M.STRETCH_SPEED, M.SPEED_ENERGY), mode === 'listening' ? M.LISTEN_ENERGY : 0);
    state.energy = Math.max(state.energy * Math.exp(-h * M.ENERGY_DECAY), floor);

    for (const point of state.trail) point.age += h;
    while (state.trail.length && state.trail[0].age > M.TRAIL_LIFE) state.trail.shift();
  }

  const speed = Math.hypot(state.vx, state.vy);
  const calm = !state.away && state.morph === 0 && state.trail.length === 0 && state.stretch < 0.002
    && Math.abs(state.scale - 1) < 0.002 && Math.abs(state.scaleVelocity) < 0.01 && state.energy < 0.02;
  const settled = calm && speed === 0 && target === null && mode !== 'listening';
  if (settled) { state.stretch = 0; state.scale = 1; state.scaleVelocity = 0; state.energy = 0; }

  let phase: IslandPhase;
  if (state.away) phase = target !== null ? 'travelling' : 'returning';
  else if (target !== null) phase = 'leaving';
  else if (landed || state.morph > 0 || Math.abs(state.scale - 1) >= 0.002) phase = 'landing';
  else phase = mode === 'listening' ? 'listening' : 'rest';

  return {
    x: state.x, y: state.y, dirX: state.dirX, dirY: state.dirY, stretch: state.stretch, scale: state.scale,
    energy: state.energy, morph: easeInOutCubic(state.morph), trail: state.trail.map(p => ({...p})),
    phase, settled,
  };
}
