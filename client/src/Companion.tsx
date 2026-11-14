import {useEffect, useRef, useState} from 'react';
import {platform} from './platform';
import {companionText, startingStatus} from './desktopStatus';

export function Companion() {
  const [status, setStatus] = useState(startingStatus);
  const [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
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
    try {await platform.showMain(); setError('');}
    catch {setError('Could not open controls. Use Artemis in the taskbar or restart the launcher.');}
  }
  return <main className="companion" aria-label="Artemis desktop status" onKeyDown={event => {
    if (event.key === 'Escape') {event.preventDefault(); void showControls();}
  }}>
    <div className="companion-copy"><h1>Artemis<span aria-hidden="true"> ·</span></h1><p id="desktop-status" role={error ? 'alert' : pollError ? 'status' : undefined} aria-live={error ? 'assertive' : pollError ? 'polite' : 'off'}>{error || pollError || companionText(status)}</p></div>
    <button ref={open} aria-describedby="desktop-status" onClick={() => void showControls()}>Open controls</button>
  </main>;
}
