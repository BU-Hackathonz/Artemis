import {describe, it, expect} from 'vitest';
import {REVEAL_MS, initialFocus, keyDecision, clickDecision} from '../prototypes/water-desktop/artemis-water@bu-hackathonz.github.io/approvalKeys.js';

// After the reveal guard: the dialog has been on screen longer than REVEAL_MS.
const LATE = 1000;

const plain = {kind: 'write_files', blocking: false};
const blocked = {kind: 'write_files', blocking: true};
const key = (name, extra = {}) => ({name, shift: false, ctrl: false, alt: false, super: false, repeated: false, ...extra});

describe('initialFocus', () => {
  it('focuses Reject when blocking and Approve otherwise', () => {
    expect(initialFocus(true)).toBe('reject');
    expect(initialFocus(false)).toBe('approve');
  });
});

describe('Escape', () => {
  it.each(['approve', 'reject', null])('rejects with focus %s', focus => {
    expect(keyDecision(key('Escape'), focus, plain, LATE)).toBe('reject');
    expect(keyDecision(key('Escape'), focus, blocked, LATE)).toBe('reject');
  });
  it('does not reject on a repeated Escape', () => {
    expect(keyDecision(key('Escape', {repeated: true}), 'approve', plain, LATE)).toBe(null);
  });
});

describe('repeated keys never decide', () => {
  it.each(['Return', 'KP_Enter', 'ISO_Enter', 'space'])('%s held down is ignored on Reject', name => {
    expect(keyDecision(key(name, {repeated: true}), 'reject', plain, LATE)).toBe(null);
  });
  it('held Shift+Enter on blocked Approve is ignored', () => {
    expect(keyDecision(key('Return', {shift: true, repeated: true}), 'approve', blocked, LATE)).toBe(null);
  });
  it('held Enter on plain Approve is ignored', () => {
    expect(keyDecision(key('space', {repeated: true}), 'approve', plain, LATE)).toBe(null);
  });
});

describe('Reject focused', () => {
  it.each(['Return', 'KP_Enter', 'ISO_Enter', 'space'])('%s rejects, blocking or not', name => {
    expect(keyDecision(key(name), 'reject', plain, LATE)).toBe('reject');
    expect(keyDecision(key(name), 'reject', blocked, LATE)).toBe('reject');
  });
  it('other keys do nothing', () => {
    expect(keyDecision(key('a'), 'reject', plain, LATE)).toBe(null);
  });
});

describe('Approve focused, not blocking', () => {
  it.each(['Return', 'KP_Enter', 'ISO_Enter', 'space'])('%s approves', name => {
    expect(keyDecision(key(name), 'approve', plain, LATE)).toBe('approve');
  });
  it('other keys do nothing', () => {
    expect(keyDecision(key('a'), 'approve', plain, LATE)).toBe(null);
    expect(keyDecision(key('Tab'), 'approve', plain, LATE)).toBe(null);
  });
});

describe('Approve focused, blocking', () => {
  it.each(['Return', 'KP_Enter', 'ISO_Enter'])('Shift+%s approves', name => {
    expect(keyDecision(key(name, {shift: true}), 'approve', blocked, LATE)).toBe('approve');
  });
  it.each(['Return', 'KP_Enter', 'ISO_Enter'])('plain %s does not approve', name => {
    expect(keyDecision(key(name), 'approve', blocked, LATE)).toBe(null);
  });
  it('space does not approve even with Shift', () => {
    expect(keyDecision(key('space'), 'approve', blocked, LATE)).toBe(null);
    expect(keyDecision(key('space', {shift: true}), 'approve', blocked, LATE)).toBe(null);
  });
  it.each(['ctrl', 'alt', 'super'])('Shift+Enter with %s does not approve', mod => {
    expect(keyDecision(key('Return', {shift: true, [mod]: true}), 'approve', blocked, LATE)).toBe(null);
  });
  it('Shift+Enter with Ctrl and Alt does not approve', () => {
    expect(keyDecision(key('KP_Enter', {shift: true, ctrl: true, alt: true}), 'approve', blocked, LATE)).toBe(null);
  });
  it('other keys do nothing', () => {
    expect(keyDecision(key('a', {shift: true}), 'approve', blocked, LATE)).toBe(null);
  });
});

describe('no focus', () => {
  it('Enter and space decide nothing', () => {
    expect(keyDecision(key('Return'), null, plain, LATE)).toBe(null);
    expect(keyDecision(key('space'), null, blocked, LATE)).toBe(null);
    expect(keyDecision(key('Return', {shift: true}), null, blocked, LATE)).toBe(null);
  });
});

describe('clickDecision', () => {
  it('reject click always rejects', () => {
    expect(clickDecision('reject', plain)).toBe('reject');
    expect(clickDecision('reject', blocked)).toBe('reject');
  });
  it('approve click approves only when not blocking', () => {
    expect(clickDecision('approve', plain)).toBe('approve');
    expect(clickDecision('approve', blocked)).toBe(null);
  });
});

describe('reveal guard', () => {
  const plain = {kind: 'write_files', blocking: false};
  const blocked = {kind: 'write_files', blocking: true};
  const enter = (extra = {}) => ({name: 'Return', shift: false, ctrl: false, alt: false, super: false, repeated: false, ...extra});
  it('is 600 ms', () => { expect(REVEAL_MS).toBe(600); });
  it('ignores a fresh Enter just after the dialog appears', () => {
    expect(keyDecision(enter(), 'approve', plain, 0)).toBe(null);
    expect(keyDecision(enter(), 'approve', plain, REVEAL_MS - 1)).toBe(null);
    expect(keyDecision(enter(), 'reject', plain, 100)).toBe(null);
    expect(keyDecision({...enter(), name: 'space'}, 'approve', plain, 100)).toBe(null);
  });
  it('accepts Enter once the guard has passed', () => {
    expect(keyDecision(enter(), 'approve', plain, REVEAL_MS)).toBe('approve');
    expect(keyDecision(enter(), 'reject', plain, REVEAL_MS + 1)).toBe('reject');
  });
  it('treats a missing or invalid time as just opened', () => {
    expect(keyDecision(enter(), 'approve', plain)).toBe(null);
    expect(keyDecision(enter(), 'approve', plain, NaN)).toBe(null);
    expect(keyDecision(enter(), 'approve', plain, -5)).toBe(null);
  });
  it('still lets Escape reject at once', () => {
    expect(keyDecision({...enter(), name: 'Escape'}, 'approve', plain, 0)).toBe('reject');
    expect(keyDecision({...enter(), name: 'Escape'}, 'approve', blocked, 0)).toBe('reject');
  });
  it('guards the blocked override too, and a held key never approves', () => {
    expect(keyDecision(enter({shift: true}), 'approve', blocked, 100)).toBe(null);
    expect(keyDecision(enter({shift: true}), 'approve', blocked, REVEAL_MS)).toBe('approve');
    expect(keyDecision(enter({repeated: true}), 'approve', plain, 5000)).toBe(null);
    expect(keyDecision(enter({shift: true, repeated: true}), 'approve', blocked, 5000)).toBe(null);
  });
});
