// Where the droplet heads while Artemis works. There is no text-caret source on Windows yet (that
// needs native code and a permission), so it follows a slow, looping path across the work area. Pure,
// so it can be tested: it always stays well inside the window, away from the screen edges.

export type FlowPoint = {x: number; y: number};

export function wanderTarget(t: number, width: number, height: number): FlowPoint {
  return {
    x: width * (0.5 + 0.38 * Math.sin(t * 0.55)),
    y: height * (0.45 + 0.3 * Math.sin(t * 0.83 + 1.2)),
  };
}

/** Which motion mode the droplet is in, from what the main window reports. An approval wins: the
 * droplet comes home and holds still, so motion is never a signal about an approval. */
export function flowMode(status: {approval: boolean; busy: boolean}): 'awaiting_approval' | 'working' | 'idle' {
  if (status.approval) return 'awaiting_approval';
  return status.busy ? 'working' : 'idle';
}

/** The droplet's target: the text caret when there is one on this screen, otherwise the slow path.
 * `caret` is already in the flow window's CSS pixels. A caret outside the window (another monitor,
 * or an application reporting nonsense) is ignored, never chased off-screen. */
export function chooseTarget(
  caret: FlowPoint | null,
  t: number,
  width: number,
  height: number,
): FlowPoint {
  if (caret && Number.isFinite(caret.x) && Number.isFinite(caret.y)
      && caret.x >= 0 && caret.x <= width && caret.y >= 0 && caret.y <= height) {
    return caret;
  }
  return wanderTarget(t, width, height);
}
