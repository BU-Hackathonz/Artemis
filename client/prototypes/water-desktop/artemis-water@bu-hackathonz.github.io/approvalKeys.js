// Pure module: no imports, no GNOME, no timers. approvalDialog.js maps each key press and click
// to these functions, so the approval rules can be tested without the shell.
//   - Escape rejects wherever the focus is.
//   - Auto-repeat (a held key) never decides anything.
//   - A blocking approval is never approved by Enter alone: Approve needs Shift+Enter.
//   - Reveal guard: for REVEAL_MS after the dialog appears, Enter and Space decide nothing, so an
//     Enter meant for something else (the request field, another window) can't approve by reflex.
//     Escape still rejects at once.

const ENTER_KEYS = ['Return', 'KP_Enter', 'ISO_Enter'];
export const REVEAL_MS = 600;

// Reject is the default when the approval is blocking, Approve otherwise.
export function initialFocus(blocking) {
  return blocking ? 'reject' : 'approve';
}

// key: {name, shift, ctrl, alt, super, repeated}. focus: 'approve' | 'reject' | null.
// sinceOpenMs: monotonic ms since the dialog was shown (a missing or invalid value counts as 0, so
// the guard holds). Returns 'approve', 'reject' or null (no decision).
export function keyDecision(key, focus, approval, sinceOpenMs = 0) {
  if (key.name === 'Escape' && !key.repeated) return 'reject';
  if (key.repeated) return null;
  if (!(Number.isFinite(sinceOpenMs) && sinceOpenMs >= REVEAL_MS)) return null;
  const enter = key.name === 'space' || ENTER_KEYS.includes(key.name);
  if (focus === 'reject') return enter ? 'reject' : null;
  if (focus === 'approve') {
    if (!approval.blocking) return enter ? 'approve' : null;
    const plainModifiers = !key.ctrl && !key.alt && !key.super;
    return ENTER_KEYS.includes(key.name) && key.shift && plainModifiers ? 'approve' : null;
  }
  return null;
}

// button: 'approve' | 'reject'. A pointer click on Approve does nothing while blocking.
export function clickDecision(button, approval) {
  if (button === 'reject') return 'reject';
  if (button === 'approve' && !approval.blocking) return 'approve';
  return null;
}
