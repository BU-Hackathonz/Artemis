import {describe, it, expect} from 'vitest';
import {appMatches, cueWindow} from '../prototypes/water-desktop/artemis-water@bu-hackathonz.github.io/ringTargets.js';

const snapCode = {id: 'code_code.desktop', name: 'Visual Studio Code'};
const debCode = {id: 'code.desktop', name: 'Visual Studio Code'};
const firefox = {id: 'firefox_firefox.desktop', name: 'Firefox'};
const urlHandler = {id: 'code_code-url-handler.desktop', name: 'Visual Studio Code - URL Handler'};

describe('appMatches: the app an "open" cue names', () => {
  it('finds VS Code by its name, its nicknames and its launcher id (deb or snap)', () => {
    for (const t of ['Visual Studio Code', 'VS Code', 'vscode', 'code'])
      for (const app of [snapCode, debCode]) expect(appMatches(t, app)).toBe(true);
  });

  it('matches other apps and the browser nickname', () => {
    expect(appMatches('Firefox', firefox)).toBe(true);
    expect(appMatches('browser', firefox)).toBe(true);
  });

  it('does not match a different app or an empty target', () => {
    expect(appMatches('Firefox', snapCode)).toBe(false);
    expect(appMatches('', snapCode)).toBe(false);
    expect(appMatches('VS Code', null)).toBe(false);
  });

  it('a name only shares a word with: no match by name alone', () => {
    expect(appMatches('Visual Studio Code', {id: 'other.desktop', name: 'Visual Studio Code - URL Handler'})).toBe(false);
  });

  it('the snap URL handler shares the launcher id part, so findApp prefers running then shown apps', () => {
    // documented: the url handler is hidden (NoDisplay), and findApp skips apps that should not show
    expect(appMatches('VS Code', urlHandler)).toBe(true);
  });
});

describe('cueWindow: is the app already on screen?', () => {
  const editor = {appId: 'code_code.desktop', title: 'app.py - Visual Studio Code', rect: {x: 0, y: 0, width: 10, height: 10}};
  it('returns the open window, or null when none matches', () => {
    expect(cueWindow({action: 'open', target: 'Visual Studio Code'}, [editor])).toBe(editor);
    expect(cueWindow({action: 'open', target: 'Firefox'}, [editor])).toBe(null);
    expect(cueWindow({action: 'open'}, [editor])).toBe(null);
    expect(cueWindow({action: 'done', target: 'editor'}, [editor])).toBe(null);
  });
});
