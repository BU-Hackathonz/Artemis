// Adapter for Tauri-only features (global shortcut, window, sidecar).
// In a plain browser these fall back to web equivalents, so the UI runs in Chrome too.
export const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

export function onPushToTalk(start: () => void, stop: () => void): () => void {
  // Browser fallback: hold Ctrl+Space while the window is focused.
  const down = (e: KeyboardEvent) => { if (e.ctrlKey && e.code === 'Space' && !e.repeat) { e.preventDefault(); start() } }
  const up = (e: KeyboardEvent) => { if (e.code === 'Space' || e.key === 'Control') stop() }
  window.addEventListener('keydown', down)
  window.addEventListener('keyup', up)
  return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up) }
}
