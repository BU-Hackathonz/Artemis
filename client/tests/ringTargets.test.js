import {describe, it, expect} from 'vitest';
import {cueTargetRect} from '../prototypes/water-desktop/artemis-water@bu-hackathonz.github.io/ringTargets.js';

const screen = {x: 0, y: 0, width: 1920, height: 1080};
const home = {x: 960, y: 40};
const editor = {appId: 'code.desktop', title: 'app.py - Visual Studio Code', rect: {x: 0, y: 40, width: 1200, height: 900}};
const terminal = {appId: 'org.gnome.Terminal.desktop', title: 'Terminal', rect: {x: 1200, y: 40, width: 720, height: 400}};
const browser = {appId: 'firefox.desktop', title: 'Docs - Mozilla Firefox', rect: {x: 300, y: 300, width: 1000, height: 700}};
const notes = {appId: 'org.gnome.TextEditor.desktop', title: 'notes.txt', rect: {x: 100, y: 100, width: 400, height: 300}};

const windows = [browser, editor, terminal];
const at = (cue, extra = {}) => cueTargetRect(cue, {windows, screen, home, ...extra});
const centre = {x: 960, y: 540, width: 0, height: 0};

describe('cueTargetRect: where the ring flies for a cue', () => {
  it('editor and file name the editor window', () => {
    expect(at({action: 'write', target: 'editor'})).toEqual(editor.rect);
    expect(at({action: 'write', target: 'file'})).toEqual(editor.rect);
  });

  it('terminal and browser name their windows by app id', () => {
    expect(at({action: 'run', target: 'terminal'})).toEqual(terminal.rect);
    expect(at({action: 'open', target: 'browser'})).toEqual(browser.rect);
  });

  it('an app or file name matches the window whose app id or title contains it', () => {
    expect(at({action: 'open', target: 'Firefox'})).toEqual(browser.rect);
    expect(at({action: 'write', target: 'app.py'})).toEqual(editor.rect);
  });

  it('the topmost window wins when several match', () => {
    const stacked = [editor, notes];   // notes is on top of the editor
    expect(cueTargetRect({action: 'write', target: 'editor'}, {windows: [...stacked], screen, home})).toEqual(notes.rect);
    expect(cueTargetRect({action: 'write', target: 'editor'}, {windows: [notes, editor], screen, home})).toEqual(editor.rect);
  });

  it('an app id match beats a title match', () => {
    const titled = {appId: 'other.app', title: 'Terminal settings', rect: {x: 1, y: 1, width: 2, height: 2}};
    expect(cueTargetRect({action: 'run', target: 'terminal'}, {windows: [terminal, titled], screen, home})).toEqual(terminal.rect);
  });

  it('done sends the ring home, whatever windows are open', () => {
    expect(at({action: 'done'})).toEqual({x: home.x, y: home.y, width: 0, height: 0});
  });

  it('no target, or one nothing matches, is the screen centre', () => {
    expect(at({action: 'think'})).toEqual(centre);
    expect(at({action: 'run', target: 'editor'}, {windows: [terminal]})).toEqual(centre);
    expect(at({action: 'open', target: 'spreadsheet'})).toEqual(centre);
    expect(at({action: 'open', target: '   '})).toEqual(centre);
  });

  it('with no windows at all, a named target is the centre, and done still goes home', () => {
    expect(cueTargetRect({action: 'write', target: 'editor'}, {windows: [], screen, home})).toEqual(centre);
    expect(cueTargetRect({action: 'done'}, {windows: [], screen})).toEqual(centre);
  });
});
