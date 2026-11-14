const modifiers = new Map([['control','Control'],['ctrl','Control'],['alt','Alt'],['shift','Shift']]);
const guidance = 'Use Control or Alt plus another modifier, for example Control+Alt+Space.';
export function normalizeVoiceShortcut(input: string): string {
  if (input.length > 80) throw new Error(guidance);
  const parts = input.split('+').map(part=>part.trim());
  if (parts.length < 3 || parts.some(part=>!part)) throw new Error(guidance);
  const key = parts.pop()!;
  const mapped = parts.map(part=>modifiers.get(part.toLowerCase()));
  const unique = new Set(mapped);
  if (mapped.some(part=>!part) || unique.size !== mapped.length || unique.size < 2 || (!unique.has('Control') && !unique.has('Alt'))) throw new Error(guidance);
  const upper = key.toUpperCase();
  if (['TAB','ESC','ESCAPE','DELETE','DEL','BACKSPACE','ENTER','RETURN','PRINTSCREEN'].includes(upper) || (upper==='F4' && unique.has('Alt'))) {
    throw new Error('That is a system shortcut. Choose a different voice shortcut.');
  }
  if (!/^(?:[A-Z0-9]|F(?:[1-9]|1\d|2[0-4])|SPACE)$/.test(upper)) throw new Error('Choose a letter, number, function key or Space after the modifiers.');
  if (unique.has('Control') && unique.has('Alt') && /^[A-Z0-9]$/.test(upper)) throw new Error('Control+Alt+letter or digit may conflict with AltGr or assistive technology. Choose a different shortcut.');
  const normalizedKey = upper === 'SPACE' ? 'Space' : upper;
  return [...['Control','Alt','Shift'].filter(part=>unique.has(part)),normalizedKey].join('+');
}
