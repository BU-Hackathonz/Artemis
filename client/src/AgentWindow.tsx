import {useEffect, useRef, useState} from 'react';
import {LiquidRing} from './LiquidRing';
import type {RingInputs} from './LiquidRing';
import {platform} from './platform';
import type {AgentStatus} from './platform';
import type {TaskState} from './contracts';
import type {TalkStatus} from './pushToTalk';

// The desktop agent island: a small transparent, always-on-top window (label "agent",
// index.html#agent) at the top centre of the screen. A frosted pill holds the liquid glass ring,
// voice bars and a chevron. It pictures the main window's state (sent as events): the ring shines
// and the bars move while Artemis works or listens. The chevron, or a click on the ring, brings back
// the main window (the controls); drag the pill to move it. Visual only: everything it shows is
// also announced in the main window.

const TOP = 8; // logical px below the top of the work area

/** Top centre of the primary screen's work area. Nothing is stored: the app keeps no browser
 *  storage, so the island starts there each launch and stays where it's dragged meanwhile. */
async function placeTopCenter() {
  const {getCurrentWindow, currentMonitor, primaryMonitor, PhysicalPosition} = await import('@tauri-apps/api/window');
  const win = getCurrentWindow();
  const size = await win.outerSize();
  const monitor = (await primaryMonitor()) ?? (await currentMonitor());
  if (!monitor) return;
  const a = monitor.workArea;
  await win.setPosition(new PhysicalPosition(Math.round(a.position.x + (a.size.width - size.width) / 2), a.position.y + Math.round(TOP * monitor.scaleFactor)));
}

const BARS = [4, 6, 4, 6, 4, 6, 4, 4];

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
      await placeTopCenter();
      const keep = (fn: () => void) => { if (active) unlisten.push(fn); else fn(); };
      keep(await platform.agentWindow.onStatus(setStatus));
      keep(await platform.agentWindow.onVisible(visible => { void (visible ? win.show() : win.hide()); }));
      keep(await platform.agentWindow.onReset(() => { void placeTopCenter(); }));
      await platform.agentWindow.ready(); // the main window answers with the state and whether to show
    })();
    return () => { active = false; unlisten.forEach(fn => fn()); document.documentElement.classList.remove('agent-page'); };
  }, []);

  const inputs: RingInputs = {connected: status.connected, taskState: status.taskState as TaskState | 'idle', microphone: status.microphone as TalkStatus['state'], approval: status.approval};
  const busy = ['queued', 'working', 'running'].includes(status.taskState) || status.microphone === 'listening' || status.microphone === 'starting';

  function open() { setPress(p => p + 1); if (platform.native) void platform.agentWindow.open(); }

  return <div className={`island${busy ? ' island-busy' : ''}${status.approval ? ' island-attention' : ''}`}
    onPointerDown={e => { if ((e.target as HTMLElement).closest('button')) return; pointer.current = {x: e.screenX, y: e.screenY, dragging: false}; }}
    onPointerMove={e => {
      const p = pointer.current;
      if (!p || p.dragging || !platform.native || Math.hypot(e.screenX - p.x, e.screenY - p.y) < 4) return;
      p.dragging = true;
      void import('@tauri-apps/api/window').then(({getCurrentWindow}) => getCurrentWindow().startDragging());
    }}
    onPointerUp={() => { pointer.current = null; }}>
    <button type="button" className="island-ring" aria-label="Open Artemis controls" onClick={open}>
      <LiquidRing inputs={inputs} kick={status.kick + press}/>
    </button>
    <span className="island-sep" aria-hidden="true"/>
    <span className="island-bars" aria-hidden="true">
      {BARS.map((h, i) => <span key={i} style={{height: `${h}px`, animationDelay: `${(i * 0.13) % 0.9}s`}}/>)}
    </span>
    <button type="button" className="island-chevron" aria-label="Open Artemis controls" onClick={open}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden="true"><path d="M5 9l7 7 7-7"/></svg>
    </button>
  </div>;
}
