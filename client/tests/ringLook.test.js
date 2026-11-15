import { describe, it, expect } from 'vitest';
import { ringLook } from '../prototypes/water-desktop/artemis-water@bu-hackathonz.github.io/ringLook.js';

const STATES = ['idle', 'listening', 'thinking', 'editing', 'running', 'speaking', 'done', 'error'];

describe('ringLook', () => {
    it('idle is the plain ring: no tint, normal spin and glow', () => {
        expect(ringLook('idle', false)).toEqual({ tint: [1, 1, 1, 0], spin: 1, glow: 0 });
    });

    it('gives every state a distinct visible tint', () => {
        const tints = STATES.filter((s) => s !== 'idle').map((s) => ringLook(s, false).tint);
        for (const t of tints) expect(t[3]).toBeGreaterThan(0);
        expect(new Set(tints.map((t) => t.join(','))).size).toBe(tints.length);
    });

    it('halves the spin while waiting for an approval', () => {
        for (const s of STATES) {
            expect(ringLook(s, true).spin).toBeCloseTo(ringLook(s, false).spin * 0.5);
        }
    });

    it('returns a fresh tint array each call', () => {
        const a = ringLook('idle', false);
        a.tint[3] = 1;
        expect(ringLook('idle', false).tint[3]).toBe(0);
    });

    it('treats an unknown state as idle', () => {
        expect(ringLook('nope', false)).toEqual(ringLook('idle', false));
    });
});
