import {useEffect, useRef, useState} from 'react';
import {LiquidRing} from './LiquidRing';
import type {RingInputs} from './LiquidRing';
import {platform} from './platform';
import type {AgentStatus} from './platform';
import type {TaskState} from './contracts';
import type {TalkStatus} from './pushToTalk';

// The desktop agent: a small transparent, always-on-top window (label "agent", index.html#agent)
// showing the liquid ring in a corner of the screen. It pictures the main window's state (sent as
// events), can be dragged anywhere, and a click brings back the controls.
// Visual only: everything it shows is also announced in the main window.

const MARGIN = 16; // logical px from the work-area corner

/** Bottom-left of the primary screen's work area. Nothing is stored: the app keeps no browser
 *  storage, so the agent starts in the corner each launch and stays where it's dragged meanwhile. */
async function placeInCorner() {
  const {getCurrentWindow, currentMonitor, primaryMonitor, PhysicalPosition} = await import('@tauri-apps/api/window');
  const win = getCurrentWindow();
  const size = await win.outerSize();
  const monitor = (await primaryMonitor()) ?? (await currentMonitor());
  if (!monitor) return;
  const a = monitor.workArea;
  const margin = Math.round(MARGIN * monitor.scaleFactor);
  await win.setPosition(new PhysicalPosition(a.position.x + margin, a.position.y + a.size.height - size.height - margin));
}

export function AgentWindow() {
  const [status, setStatus] = useState<AgentStatus>({connected: false, taskState: 'idle', microphone: 'idle', approval: false, kick: 0});
  const [press, setPress] = useState(0);
  const pointer = useRef<{x: number; y: number; dragging: boolean} | null>(null);

  useEffect(() => {
    document.documentElement.classList.add('agent-page');
    if (!platform.native) return () => document.documentElement.classList.remove('agent-page');
    const unlisten: (() => void)[] = [];
    let active = true;
    void (async () => {
      const {getCurrentWindow} = await import('@tauri-apps/api/window');
      const win = getCurrentWindow();
      await placeInCorner();
      const keep = (fn: () => void) => { if (active) unlisten.push(fn); else fn(); };
      keep(await platform.agentWindow.onStatus(setStatus));
      keep(await platform.agentWindow.onVisible(visible => { void (visible ? win.show() : win.hide()); }));
      keep(await platform.agentWindow.onReset(() => { void placeInCorner(); }));
      await platform.agentWindow.ready(); // the main window answers with the state and whether to show
    })();
    return () => { active = false; unlisten.forEach(fn => fn()); document.documentElement.classList.remove('agent-page'); };
  }, []);

  const inputs: RingInputs = {connected: status.connected, taskState: status.taskState as TaskState | 'idle', microphone: status.microphone as TalkStatus['state'], approval: status.approval};

  function open() { setPress(p => p + 1); if (platform.native) void platform.agentWindow.open(); }

  return <button type="button" className="agent" aria-label="Open Artemis controls"
    onPointerDown={e => { pointer.current = {x: e.screenX, y: e.screenY, dragging: false}; }}
    onPointerMove={e => {
      const p = pointer.current;
      if (!p || p.dragging || !platform.native || Math.hypot(e.screenX - p.x, e.screenY - p.y) < 4) return;
      p.dragging = true;
      void import('@tauri-apps/api/window').then(({getCurrentWindow}) => getCurrentWindow().startDragging());
    }}
    onPointerUp={() => { const p = pointer.current; pointer.current = null; if (p && !p.dragging) open(); }}
    onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } }}>
    <LiquidRing inputs={inputs} kick={status.kick + press}/>
  </button>;
}
