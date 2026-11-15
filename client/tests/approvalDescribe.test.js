import {describe, it, expect} from 'vitest';
import {describeApproval, accessibleName, UNKNOWN_MESSAGE} from '../prototypes/water-desktop/artemis-water@bu-hackathonz.github.io/approvalDescribe.js';
import {keyDecision, clickDecision} from '../prototypes/water-desktop/artemis-water@bu-hackathonz.github.io/approvalKeys.js';

const LATE = 1000;
const key = (name, extra = {}) => ({name, shift: false, ctrl: false, alt: false, super: false, repeated: false, ...extra});
const desktop = (kind, d, extra = {}) => ({kind, summary: 'Summary.', blocking: false, desktop: d, ...extra});

describe('describeApproval, existing kinds', () => {
  it('write_files keeps its title and file lines', () => {
    const r = describeApproval({kind: 'write_files', files: [{path: 'a.py', action: 'created', lines_added: 3, lines_removed: 0}]});
    expect(r).toEqual({title: 'Review file changes', known: true, lines: ['a.py: created, 3 lines added, 0 removed.']});
  });
  it('run_program keeps its title and run line', () => {
    const r = describeApproval({kind: 'run_program', run: {entry: 'main.py', preset: 'safe', timeout_s: 10}});
    expect(r).toEqual({title: 'Review program run', known: true, lines: ['Run main.py with safe. Timeout: 10 seconds.']});
  });
  it('run_program with no run is unknown', () => {
    expect(describeApproval({kind: 'run_program'}).known).toBe(false);
  });
});

describe('describeApproval, desktop kinds', () => {
  it('run_command shows the exact command and folder', () => {
    const cmd = 'rm -rf  "$HOME/x"  &&   echo  "done"\nls';
    const r = describeApproval(desktop('run_command', {action: 'run_command', target: '~/My  Projects', text: cmd}));
    expect(r.title).toBe('Run a command');
    expect(r.known).toBe(true);
    expect(r.lines).toContain(`Command: ${cmd}`);
    expect(r.lines).toContain('Folder: ~/My  Projects');
  });
  it('type shows the exact text and target', () => {
    const r = describeApproval(desktop('type', {action: 'type_text', target: 'the focused window', text: 'hello  <b>world</b> 100%'}));
    expect(r.title).toBe('Type into a window');
    expect(r.lines).toEqual(['Text: hello  <b>world</b> 100%', 'Into: the focused window']);
  });
  it('open and read_outside show the target', () => {
    expect(describeApproval(desktop('open', {action: 'open_app', target: 'VS Code'}))).toEqual({title: 'Open', known: true, lines: ['Open: VS Code']});
    expect(describeApproval(desktop('open', {action: 'open_path', target: '~/notes.txt', text: 'Text Editor'})).lines)
      .toEqual(['Open: ~/notes.txt', 'With: Text Editor']);
    expect(describeApproval(desktop('read_outside', {action: 'read_path', target: '~/Documents/plan.md'})))
      .toEqual({title: 'Read a file', known: true, lines: ['File: ~/Documents/plan.md']});
  });
  it('a command or typed text that is missing is unknown, never shown blind', () => {
    expect(describeApproval(desktop('run_command', {action: 'run_command', target: '~'})).known).toBe(false);
    expect(describeApproval(desktop('type', {action: 'press_keys', target: 'x', text: null})).known).toBe(false);
  });
});

describe('describeApproval, the approvals the gate really sends (policy/desktop.py)', () => {
  const SHA = 'a'.repeat(64);
  it('open_app: the app id and its launcher file', () => {
    expect(describeApproval(desktop('open', {action: 'open_app', target: 'org.gnome.TextEditor.desktop', sha256: SHA})))
      .toEqual({title: 'Open', known: true, lines: ['Open: org.gnome.TextEditor.desktop']});
  });
  it('open_path with an app: the resolved path and the app id', () => {
    expect(describeApproval(desktop('open', {action: 'open_path', target: '/home/u/notes.md', text: 'org.gnome.TextEditor.desktop', sha256: SHA})))
      .toEqual({title: 'Open', known: true, lines: ['Open: /home/u/notes.md', 'With: org.gnome.TextEditor.desktop']});
  });
  it('open_path without an app: the path alone', () => {
    expect(describeApproval(desktop('open', {action: 'open_path', target: '/home/u/Documents', text: null, sha256: null})).lines)
      .toEqual(['Open: /home/u/Documents']);
  });
  it('read_path: the resolved path', () => {
    expect(describeApproval(desktop('read_outside', {action: 'read_path', target: '/home/u/Documents/plan.md', text: null, sha256: SHA})).lines)
      .toEqual(['File: /home/u/Documents/plan.md']);
  });
  it('run_command: the exact command and its folder', () => {
    expect(describeApproval(desktop('run_command', {action: 'run_command', target: '/home/u/project', text: 'ls -la'})))
      .toEqual({title: 'Run a command', known: true, lines: ['Command: ls -la', 'Folder: /home/u/project']});
  });
  it('type_text: the exact text and the window', () => {
    expect(describeApproval(desktop('type', {action: 'type_text', target: 'org.gnome.TextEditor.desktop: notes.txt', text: 'Dear Sam,'})))
      .toEqual({title: 'Type into a window', known: true, lines: ['Text: Dear Sam,', 'Into: org.gnome.TextEditor.desktop: notes.txt']});
  });
  it('press_keys: the keys, not text to type', () => {
    expect(describeApproval(desktop('type', {action: 'press_keys', target: 'org.gnome.TextEditor.desktop: notes.txt', text: 'ctrl+s'})))
      .toEqual({title: 'Press keys in a window', known: true, lines: ['Keys: ctrl+s', 'Into: org.gnome.TextEditor.desktop: notes.txt']});
  });
  it('a desktop action that does not belong to its kind is unknown', () => {
    expect(describeApproval(desktop('run_command', {action: 'type_text', target: '~', text: 'rm -rf x'})).known).toBe(false);
    expect(describeApproval(desktop('read_outside', {action: 'open_path', target: '~/a', text: null})).known).toBe(false);
    expect(describeApproval(desktop('open', {action: 'press_keys', target: 'x', text: 'ctrl+s'})).known).toBe(false);
  });
});

describe('describeApproval, unknown', () => {
  it.each([
    ['unknown kind', {kind: 'delete_everything', desktop: {action: 'x', target: 'y', text: 'z'}}],
    ['desktop kind without desktop', {kind: 'run_command'}],
    ['desktop kind with null desktop', {kind: 'type', desktop: null}],
    ['desktop with a non-string target', {kind: 'open', desktop: {action: 'open_app', target: 5}}],
    ['inherited property as kind', {kind: 'toString', desktop: {target: 'a', text: 'b'}}],
    ['not an object', null],
  ])('%s', (_n, a) => {
    const r = describeApproval(a);
    expect(r.known).toBe(false);
    expect(r.lines).toEqual([UNKNOWN_MESSAGE]);
  });
});

describe('accessibleName', () => {
  it('includes the exact command', () => {
    const a = desktop('run_command', {action: 'run_command', target: '~/app', text: 'npm  test --  -t "x y"'});
    const name = accessibleName(a);
    expect(name).toContain('Run a command');
    expect(name).toContain('Summary.');
    expect(name).toContain('Command: npm  test --  -t "x y"');
    expect(name).toContain('Folder: ~/app');
  });
  it('includes the exact typed text', () => {
    expect(accessibleName(desktop('type', {action: 'type_text', target: 'the focused window', text: 'Dear Sam,'})))
      .toContain('Text: Dear Sam,');
  });
  it('for an unknown approval says it is being rejected', () => {
    expect(accessibleName({kind: 'mystery'})).toContain(UNKNOWN_MESSAGE);
  });
});

describe('an approval the dialog cannot show can never be approved', () => {
  const unknowns = [{kind: 'mystery', blocking: false}, {kind: 'type', blocking: false}, {kind: 'run_command', blocking: true, desktop: null}];
  it.each(unknowns)('keyDecision never approves %o', a => {
    for (const focus of ['approve', 'reject', null])
      for (const name of ['Return', 'KP_Enter', 'ISO_Enter', 'space', 'a'])
        for (const shift of [false, true])
          expect(keyDecision(key(name, {shift}), focus, a, LATE)).not.toBe('approve');
  });
  it.each(unknowns)('Enter and Shift+Enter reject %o, Escape too', a => {
    for (const focus of ['approve', 'reject'])
      for (const shift of [false, true])
        expect(keyDecision(key('Return', {shift}), focus, a, LATE)).toBe('reject');
    expect(keyDecision(key('Escape'), 'approve', a, 0)).toBe('reject');
  });
  it('the reveal guard and held keys still apply', () => {
    expect(keyDecision(key('Return'), 'reject', unknowns[0], 100)).toBe(null);
    expect(keyDecision(key('Return', {repeated: true}), 'reject', unknowns[0], LATE)).toBe(null);
  });
  it.each(unknowns)('clickDecision never approves %o', a => {
    expect(clickDecision('approve', a)).toBe(null);
    expect(clickDecision('reject', a)).toBe('reject');
  });
  it('a known desktop approval still approves with Enter', () => {
    const a = desktop('run_command', {action: 'run_command', target: '~', text: 'ls'});
    expect(keyDecision(key('Return'), 'approve', a, LATE)).toBe('approve');
  });
});
