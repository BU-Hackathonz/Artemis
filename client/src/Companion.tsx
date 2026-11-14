import {useEffect, useRef, useState} from 'react';
import {LiquidRing} from './LiquidRing';
import {platform} from './platform';
import {companionText, startingStatus} from './desktopStatus';

// Resting heights of the eight voice bars, as in the GNOME island and the first Windows island. Eight
// 3 px bars with 4 px gaps are 52 px wide, the width the notch has always reserved for its indicator.
const BARS = [4, 6, 4, 6, 4, 6, 4, 4];

export function Companion() {
  const [status, setStatus] = useState(startingStatus);
  const [pollError, setPollError] = useState('');
  const [actionError, setActionError] = useState('');
  const error = actionError || pollError;
  const open = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    document.documentElement.classList.add('companion-page');
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const next = await platform.getCompanionStatus();
        if (active) {setStatus(next); setPollError('');}
      } catch {if (active) setPollError('Open controls to check Artemis.');}
      if (active) timer = setTimeout(() => void refresh(), 750);
    }
    void refresh();
    open.current?.focus();
    return () => {active = false; clearTimeout(timer); document.documentElement.classList.remove('companion-page');};
  }, []);
  async function showControls() {
    try {await platform.showSettings(); setActionError('');}
    catch {setActionError('Could not open settings. Use Artemis in the taskbar or restart the launcher.');}
  }
  const text = error || companionText(status);
  const listening = status.microphone === 'listening';
  const working = status.task === 'queued' || status.task === 'working' || status.task === 'running';
  // The ring is decorative: the status text (read by screen readers) carries the same meaning.
  const tone = error ? 'error' : status.approval ? 'attention' : listening ? 'listening' : status.connection === 'ready' ? 'ready' : 'pending';
  return <main className="companion" data-tone={tone} aria-label="Artemis desktop status" onKeyDown={event => {
    if (event.key === 'Escape') {event.preventDefault(); void showControls();}
  }}>
    <h1 className="companion-heading">Artemis desktop status</h1>
    <LiquidRing kick={0} inputs={{connected: !error && status.connection === 'ready', taskState: status.task, microphone: status.microphone, approval: status.approval}}/>
    {/* The island's voice bars (decorative; the status text carries the meaning). They move while
        Artemis works or listens, and are still under reduced motion. */}
    <span className="companion-bars" data-active={listening || working ? 'true' : undefined} aria-hidden="true">
      {BARS.map((h, i) => <span key={i} style={{height: `${h}px`, animationDelay: `${(i * 0.13) % 0.9}s`}}/>)}
    </span>
    <p id="companion-status" className="companion-status" title={text} role={error ? 'alert' : undefined} aria-live={error ? 'assertive' : 'off'}>{text}</p>
    <button ref={open} className="companion-open" title={text} aria-label="Open settings and controls" aria-describedby="companion-status" onClick={() => void showControls()}>
      <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M4 6.5 8 10.5 12 6.5" /></svg>
    </button>
  </main>;
}
