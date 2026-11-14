import { useRef, useState } from 'react';
import { Button, Dialog, Heading, Modal, ModalOverlay } from 'react-aria-components';
import { HttpError } from './api';
import type { Approval, Decision } from './contracts';
// After a background reveal, Enter on Approve is ignored for this long so a reflex Enter cannot approve. Escape is never guarded.
export const ENTER_GUARD_MS = 600;
export function ApprovalDialog({approval, decide, enterGuardUntil}: {approval: Approval; enterGuardUntil?: number; decide: (decision: Decision) => Promise<void>}) {
  const [busy, setBusy] = useState(false);
  const [stale, setStale] = useState(false);
  const [error, setError] = useState('');
  const guardedEnter = useRef(false);
  async function send(decision: Decision) {
    if (busy || stale) return;
    setBusy(true); setError('');
    try { await decide(decision); } catch (e) { if (e instanceof HttpError && e.status === 409) setStale(true); setError(e instanceof Error ? e.message : 'Decision failed. Please try again.'); }
    finally { setBusy(false); }
  }
  return <ModalOverlay isOpen isDismissable={false} isKeyboardDismissDisabled={busy || stale} onOpenChange={open => {if (!open) void send('reject');}}>
    <Modal><Dialog aria-describedby="approval-summary" data-approval-id={approval.approval_id}>
      <Heading slot="title">{approval.kind === 'write_files' ? 'Review file changes' : 'Review program run'}</Heading>
      <p id="approval-summary">{approval.summary}</p>
      {approval.files.length > 0 && <ul>{approval.files.map(f => <li key={f.path}>{f.path}: {f.action}, {f.lines_added} lines added, {f.lines_removed} removed.</li>)}</ul>}
      {approval.run && <p>Run {approval.run.entry} with {approval.run.preset}. Timeout: {approval.run.timeout_s} seconds.</p>}
      {approval.findings.length > 0 && <section aria-label="Security findings"><h3>Security findings</h3><ul>{approval.findings.map((f, i) => <li key={i}>{f.severity}: {f.rule} in {f.path}, line {f.line}: {f.message}</li>)}</ul></section>}
      {approval.diff && <details><summary>Read the proposed diff</summary><pre tabIndex={0} aria-label="Proposed diff">{approval.diff}</pre></details>}
      {approval.blocking ? <p role="note">Blocked action. Review the security findings. To override, focus Approve and press Shift+Enter. Enter alone cannot approve. Escape rejects.</p> : <p>Choose a button and press Enter. Escape rejects this proposal.</p>}
      <div className="actions"><Button data-approval-reject autoFocus={Boolean(approval.blocking)} isDisabled={busy || stale} onPress={() => void send('reject')}>Reject</Button><Button data-approval-approve autoFocus={!approval.blocking} isDisabled={busy || stale} aria-keyshortcuts={approval.blocking ? 'Shift+Enter' : undefined} onKeyDown={event => {
        if (approval.blocking && event.key === 'Enter' && event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey && !event.repeat) { event.preventDefault(); event.stopPropagation(); void send('approve'); }
        else { guardedEnter.current = !approval.blocking && event.key === 'Enter' && enterGuardUntil !== undefined && Date.now() < enterGuardUntil; event.continuePropagation(); }
      }} onPress={event => {
        // usePress starts on keydown whatever the handler above does, so the guard is applied to the press that Enter completes.
        const ignored = event.pointerType === 'keyboard' && guardedEnter.current;
        guardedEnter.current = false;
        if (!approval.blocking && !ignored) void send('approve');
      }}>Approve{approval.blocking ? ' blocked action (Shift+Enter)' : ''}</Button></div>
      {busy && <p role="status">Sending decision…</p>}
      {error && <p role="alert">{error}</p>}
      {stale && <Button onPress={() => window.location.reload()}>Disconnect and reset</Button>}
    </Dialog></Modal>
  </ModalOverlay>;
}
