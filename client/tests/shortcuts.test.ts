import {describe,it,expect} from 'vitest';
import {normalizeVoiceShortcut} from '../src/shortcuts';
describe('voice shortcut validation',()=>{
  it.each(['Control+Alt+N','Alt+Control+v','Control+Alt+Shift+A','Shift+A','Control+C','Alt+F4','Control+Shift+Escape','Control+Alt+Delete','Control+Alt+F4','Control+Alt+Tab','Alt+Shift+Tab','Control+Ctrl+A','Super+Shift+A','Control+Alt+Insert','Control++Space'])('rejects unsafe or ambiguous chord %s',value=>{
    expect(()=>normalizeVoiceShortcut(value)).toThrow();
  });
  it.each([
    ['Control+Alt+Space','Control+Alt+Space'],
    ['ctrl + shift + space','Control+Shift+Space'],
    ['Shift+Alt+F9','Alt+Shift+F9'],
  ])('normalizes supported chord %s',(input,output)=>{
    expect(normalizeVoiceShortcut(input)).toBe(output);
  });
});
