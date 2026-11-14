import {useEffect, useRef, useState} from 'react';
import {LiquidRing} from './LiquidRing';
import {platform} from './platform';
import {companionText, startingStatus} from './desktopStatus';

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
  // The ring is decorative: the status text (read by screen readers) carries the same meaning.
  const tone = error ? 'error' : status.approval ? 'attention' : listening ? 'listening' : status.connection === 'ready' ? 'ready' : 'pending';
  return <main className="companion" data-tone={tone} aria-label="Artemis desktop status" onKeyDown={event => {
    if (event.key === 'Escape') {event.preventDefault(); void showControls();}
  }}>
    <h1 className="companion-heading">Artemis desktop status</h1>
    <LiquidRing kick={0} inputs={{connected: !error && status.connection === 'ready', taskState: status.task, microphone: status.microphone, approval: status.approval}}/>
    <svg className="companion-wave" data-listening={listening ? 'true' : undefined} viewBox="0 0 40 16" aria-hidden="true" focusable="false">
      <path d="M1 8 C 5 2, 9 14, 13 8 S 21 2, 25 8 S 33 14, 37 8 S 39 6, 39 8" />
    </svg>
    <p id="companion-status" className="companion-status" title={text} role={error ? 'alert' : undefined} aria-live={error ? 'assertive' : 'off'}>{text}</p>
    <button ref={open} className="companion-open" title={text} aria-label="Open settings and controls" aria-describedby="companion-status" onClick={() => void showControls()}>
      <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M4 6.5 8 10.5 12 6.5" /></svg>
    </button>
  </main>;
}
