// How the agent's ring looks in each island state: tint (alpha 0 = no tint), spin (multiplier on
// the swirl) and glow (extra halo). Pure: no GNOME imports, so client/tests/ringLook.test.js can
// import it. Idle is today's plain ring; waiting for an approval halves the spin.

const LOOKS = {
    idle: {tint: [1, 1, 1, 0], spin: 1, glow: 0},
    listening: {tint: [0.35, 0.85, 1.0, 0.35], spin: 1.5, glow: 0.3},
    thinking: {tint: [0.85, 0.6, 1.0, 0.3], spin: 3, glow: 0.45},
    editing: {tint: [1.0, 0.78, 0.25, 0.35], spin: 2.2, glow: 0.4},
    running: {tint: [0.2, 0.9, 0.8, 0.35], spin: 2.6, glow: 0.42},
    speaking: {tint: [1.0, 0.5, 0.75, 0.3], spin: 1.8, glow: 0.38},
    done: {tint: [0.4, 1.0, 0.55, 0.4], spin: 1.0, glow: 0.5},
    error: {tint: [1.0, 0.35, 0.35, 0.4], spin: 0.8, glow: 0.5},
};

const WAITING_SPIN = 0.5;   // an approval is open: the ring turns slowly

/** The look for an island state (unknown states look idle). Returns a fresh object. */
export function ringLook(state, waiting) {
    const base = LOOKS[state] ?? LOOKS.idle;
    return {
        tint: [...base.tint],
        spin: base.spin * (waiting ? WAITING_SPIN : 1),
        glow: base.glow,
    };
}
