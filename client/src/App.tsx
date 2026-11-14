import { useEffect, useReducer, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { createHttpCore, HttpError } from './api';
import { createDemoCore } from './demo';
import { ApprovalDialog } from './ApprovalDialog';
import { initialState, reduceEvent } from './state';
import { terminal } from './contracts';
import type { Core, CoreEvent, Health } from './contracts';
import { platform } from './platform';

interface Connection {core: Core; sid: string; health: Health; demo: boolean}
const message = (e: unknown) => e instanceof TypeError ? 'Artemis core is not reachable. Start the core, check its address and token, then reconnect.' : e instanceof Error ? e.message : 'Request failed. Check the core and try again.';
function eventText(event: CoreEvent) {
  if (event.type === 'voice.status' && typeof event.data.voice_ready === 'boolean') return event.data.voice_ready ? 'Core speech is available.' : 'Core speech is unavailable. Screen reader announcements enabled.';
  if (event.announce?.text) return event.announce.text;
  if (typeof event.data.text === 'string') return event.data.text;
  if (typeof event.data.message === 'string') return event.data.message;
  return event.type.replaceAll('.', ' ');
}
export function App() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [status, setStatus] = useState('Not connected');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [address, setAddress] = useState('http://127.0.0.1:8000');
  const [token, setToken] = useState('');
  const [text, setText] = useState('');
  const [state, dispatch] = useReducer(reduceEvent, initialState);
  const requestRef = useRef<{text: string; id: string} | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const hadApproval = useRef(false);
  const focusedSession = useRef<string | null>(null);
  const [submittedTask, setSubmittedTask] = useState<string | null>(null);
  const generation = useRef(0);
  const voiceReady = state.voiceReady ?? connection?.health.voice_ready ?? false;
  const ready = status === 'Connected' || status === 'Demo connected';
  useEffect(() => {
    if (!connection) return;
    return connection.core.subscribe(connection.sid, dispatch, setStatus);
  }, [connection]);
  useEffect(() => {
    if (!connection || !ready || focusedSession.current === connection.sid) return;
    focusedSession.current = connection.sid;
    const frame = requestAnimationFrame(() => {
      if (!document.querySelector('[role="dialog"]')) input.current?.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [connection, ready]);
  useEffect(() => {
    function restoreRequestFocus() {
      const active = document.activeElement;
      if (ready && state.approvals.length === 0 && (!active || active === document.body || active === document.documentElement)) input.current?.focus();
    }
    window.addEventListener('focus', restoreRequestFocus);
    return () => window.removeEventListener('focus', restoreRequestFocus);
  }, [ready, state.approvals.length]);
  useEffect(() => { if (state.transcript !== null) setText(state.transcript); }, [state.transcript]);
  useEffect(() => { if (state.taskId === submittedTask && terminal(state.taskState)) setSubmittedTask(null); }, [state.taskId, state.taskState, submittedTask]);
  useEffect(() => {
    const open = state.approvals.length > 0;
    if (hadApproval.current && !open && ready) {
      const frame = requestAnimationFrame(() => input.current?.focus());
      hadApproval.current = open;
      return () => cancelAnimationFrame(frame);
    }
    hadApproval.current = open;
  }, [state.approvals.length, ready]);
  useEffect(() => () => { generation.current++; }, []);
  async function connect(demo: boolean, blockedRun = false) {
    setBusy(true); setError('');
    const attempt = ++generation.current;
    try {
      const core = demo ? createDemoCore({blockedRun}) : createHttpCore(address, token);
      const health = await core.health();
      if (!health.ok) throw new Error('Core is not healthy. Restart it and reconnect.');
      const sid = await core.session();
      if (attempt !== generation.current) return;
      setConnection({core, sid, health, demo}); setToken(''); setStatus('Connecting event stream…');
    } catch (e) {setError(message(e));}
    finally {if (attempt === generation.current) setBusy(false);}
  }
  async function command(action: () => Promise<unknown>) {
    setError('');
    try { await action(); } catch (e) { setError(message(e)); }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!connection || !ready || busy || !text.trim() || ((submittedTask !== null || !terminal(state.taskState)) && state.taskState !== 'awaiting_reply')) return;
    setBusy(true); setError('');
    try {
      if (state.taskState === 'awaiting_reply' && state.taskId) await connection.core.reply(state.taskId, text.trim());
      else {
        if (!requestRef.current || requestRef.current.text !== text.trim()) requestRef.current = {text:text.trim(), id:crypto.randomUUID()};
        const taskId = await connection.core.submit(connection.sid, text.trim(), requestRef.current.id);
        setSubmittedTask(taskId);
      }
      requestRef.current = null; setText('');
    } catch (e) {
      if (e instanceof HttpError && e.code === 'task_active' && e.taskId) setSubmittedTask(e.taskId);
      setError(message(e));
    }
    finally {setBusy(false);}
  }
  return <><a className="skip" href="#request">Skip to request</a><header><p>ARTEMIS · {platform.name}</p><h1>Build with words.</h1><p>Direct a change, review each action, and hear the result.</p></header>
    <main>
      <section aria-labelledby="connection-heading"><h2 id="connection-heading">Core connection</h2>
        <p role="status">{status}</p>
        {!connection ? <><form onSubmit={e => {e.preventDefault(); void connect(false);}}>
          <label htmlFor="address">Core address</label><input autoFocus id="address" type="url" value={address} onChange={e => setAddress(e.target.value)} required disabled={busy}/>
          <label htmlFor="token">Launch token</label><input id="token" type="password" value={token} onChange={e => setToken(e.target.value)} required autoComplete="off" disabled={busy}/>
          <button disabled={busy}>Connect to core</button>
        </form><p>Launch the core first. Your token stays in memory for this connection.</p><button disabled={busy} onClick={() => void connect(true)}>Try in-memory demo</button> <button disabled={busy} onClick={() => void connect(true, true)}>Try blocked-action demo</button><p>The blocked-action demo adds a synthetic security finding for testing the approval shortcut. Both demos stay in memory.</p></> : <>
          <p>{connection.demo ? 'Demo only: synthetic Hello World proposals. No files, code execution or microphone access.' : `Core ${connection.health.version}. ${voiceReady ? 'Core handles spoken announcements.' : 'Screen reader live announcements enabled.'}`}</p>
          <button onClick={() => window.location.reload()}>Disconnect and reset</button>
          {!ready && <button onClick={() => setConnection({...connection})}>Reconnect event stream</button>}
        </>}
      </section>
      {error && <p role="alert">{error}</p>}
      <div className="workspace">
        <section aria-labelledby="request-heading"><h2 id="request-heading">Your request</h2>
          <p>Task: {state.taskState.replaceAll('_', ' ')}</p>
          <form onSubmit={e => void submit(e)}>
            <label htmlFor="request">{state.taskState === 'awaiting_reply' ? 'Answer the clarification question' : 'What would you like to build?'}</label>
            <p id="request-keys">Enter sends your request. Shift+Enter adds a new line.</p>
            <textarea ref={input} id="request" aria-describedby="request-keys" onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey && !event.altKey && !event.ctrlKey && !event.metaKey && !event.nativeEvent.isComposing) {
                event.preventDefault(); event.currentTarget.form?.requestSubmit();
              }
            }} rows={4} value={text} onChange={e => setText(e.target.value)} placeholder="Create a Python program that prints Hello World" disabled={!connection || !ready} required/>
            <button disabled={!connection || !ready || busy || !text.trim() || ((submittedTask !== null || !terminal(state.taskState)) && state.taskState !== 'awaiting_reply')}>{state.taskState === 'awaiting_reply' ? 'Send reply' : 'Send request'}</button>
          </form>
          <div className="actions"><button disabled={!ready || !(submittedTask ?? state.taskId) || (!submittedTask && terminal(state.taskState)) || busy} onClick={() => void command(() => connection!.core.cancel((submittedTask ?? state.taskId)!))}>Cancel task</button>
            <button disabled={!ready || connection?.demo} onClick={() => void command(() => connection!.core.speech('repeat'))}>Repeat last speech</button>
            <button disabled={!ready || connection?.demo} onClick={() => void command(() => connection!.core.speech('stop'))}>Stop speech</button></div>
        </section>
        <section aria-labelledby="log-heading"><h2 id="log-heading">Activity log</h2><p>Latest 500 events. Tab into the log and use arrow keys to scroll.</p>
          <div role="log" aria-label="Activity log" aria-live={voiceReady ? 'off' : 'polite'} aria-relevant="additions" tabIndex={0} className="log">
            {state.events.length === 0 ? <p>Events will appear here.</p> : state.events.map((event, index) => <article key={`${event.seq}-${index}`}><p><strong>{eventText(event)}</strong></p>
              {event.type === 'run.result' && <><p>Exit code: {String(event.data.exit_code)}{event.data.timed_out ? ' · Timed out' : ''}{event.data.truncated ? ' · Output truncated' : ''}</p><pre>{String(event.data.stdout_tail ?? '')}{String(event.data.stderr_tail ?? '')}</pre></>}
              {event.type === 'error' && typeof event.data.recovery === 'string' && <p>{event.data.recovery}</p>}
            </article>)}
          </div>
        </section>
      </div>
      {state.approvals[0] && connection && ready && <ApprovalDialog key={state.approvals[0].approval.approval_id} approval={state.approvals[0].approval} decide={decision => connection.core.decide(state.approvals[0].approval, decision)}/>}
    </main><footer>Writes and runs require approval. Review every proposal before approving.</footer></>;
}
