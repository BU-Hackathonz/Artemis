import {describe, it, expect} from 'vitest';
import {initialFocus, keyDecision, clickDecision} from '../prototypes/water-desktop/artemis-water@bu-hackathonz.github.io/approvalKeys.js';

const plain = {blocking: false};
const blocked = {blocking: true};
const key = (name, extra = {}) => ({name, shift: false, ctrl: false, alt: false, super: false, repeated: false, ...extra});

describe('initialFocus', () => {
  it('focuses Reject when blocking and Approve otherwise', () => {
    expect(initialFocus(true)).toBe('reject');
    expect(initialFocus(false)).toBe('approve');
  });
});

describe('Escape', () => {
  it.each(['approve', 'reject', null])('rejects with focus %s', focus => {
    expect(keyDecision(key('Escape'), focus, plain)).toBe('reject');
    expect(keyDecision(key('Escape'), focus, blocked)).toBe('reject');
  });
  it('does not reject on a repeated Escape', () => {
    expect(keyDecision(key('Escape', {repeated: true}), 'approve', plain)).toBe(null);
  });
});

describe('repeated keys never decide', () => {
  it.each(['Return', 'KP_Enter', 'ISO_Enter', 'space'])('%s held down is ignored on Reject', name => {
    expect(keyDecision(key(name, {repeated: true}), 'reject', plain)).toBe(null);
  });
  it('held Shift+Enter on blocked Approve is ignored', () => {
    expect(keyDecision(key('Return', {shift: true, repeated: true}), 'approve', blocked)).toBe(null);
  });
  it('held Enter on plain Approve is ignored', () => {
    expect(keyDecision(key('space', {repeated: true}), 'approve', plain)).toBe(null);
  });
});

describe('Reject focused', () => {
  it.each(['Return', 'KP_Enter', 'ISO_Enter', 'space'])('%s rejects, blocking or not', name => {
    expect(keyDecision(key(name), 'reject', plain)).toBe('reject');
    expect(keyDecision(key(name), 'reject', blocked)).toBe('reject');
  });
  it('other keys do nothing', () => {
    expect(keyDecision(key('a'), 'reject', plain)).toBe(null);
  });
});

describe('Approve focused, not blocking', () => {
  it.each(['Return', 'KP_Enter', 'ISO_Enter', 'space'])('%s approves', name => {
    expect(keyDecision(key(name), 'approve', plain)).toBe('approve');
  });
  it('other keys do nothing', () => {
    expect(keyDecision(key('a'), 'approve', plain)).toBe(null);
    expect(keyDecision(key('Tab'), 'approve', plain)).toBe(null);
  });
});

describe('Approve focused, blocking', () => {
  it.each(['Return', 'KP_Enter', 'ISO_Enter'])('Shift+%s approves', name => {
    expect(keyDecision(key(name, {shift: true}), 'approve', blocked)).toBe('approve');
  });
  it.each(['Return', 'KP_Enter', 'ISO_Enter'])('plain %s does not approve', name => {
    expect(keyDecision(key(name), 'approve', blocked)).toBe(null);
  });
  it('space does not approve even with Shift', () => {
    expect(keyDecision(key('space'), 'approve', blocked)).toBe(null);
    expect(keyDecision(key('space', {shift: true}), 'approve', blocked)).toBe(null);
  });
  it.each(['ctrl', 'alt', 'super'])('Shift+Enter with %s does not approve', mod => {
    expect(keyDecision(key('Return', {shift: true, [mod]: true}), 'approve', blocked)).toBe(null);
  });
  it('Shift+Enter with Ctrl and Alt does not approve', () => {
    expect(keyDecision(key('KP_Enter', {shift: true, ctrl: true, alt: true}), 'approve', blocked)).toBe(null);
  });
  it('other keys do nothing', () => {
    expect(keyDecision(key('a', {shift: true}), 'approve', blocked)).toBe(null);
  });
});

describe('no focus', () => {
  it('Enter and space decide nothing', () => {
    expect(keyDecision(key('Return'), null, plain)).toBe(null);
    expect(keyDecision(key('space'), null, blocked)).toBe(null);
    expect(keyDecision(key('Return', {shift: true}), null, blocked)).toBe(null);
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
