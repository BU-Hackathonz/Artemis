import { useState } from 'react';
import { Button, Dialog, Heading, Modal, ModalOverlay } from 'react-aria-components';
import { HttpError } from './api';
import { DESKTOP_KINDS } from './contracts';
import type { Approval, Decision } from './contracts';

const DESKTOP_TITLES: Record<string, string> = {open: 'Review open request', read_outside: 'Review file read', run_command: 'Review command run', type: 'Review typing'};
// Desktop kinds need their spec to be shown; unknown kinds can't be shown at all.
export function canShow(approval: Approval): boolean {
  const kind: string = approval.kind;
  if (kind === 'write_files' || kind === 'run_program') return true;
  return (DESKTOP_KINDS as readonly string[]).includes(kind) && typeof approval.desktop?.action === 'string' && typeof approval.desktop.target === 'string';
}
// The text the dialog and its accessible description show for a desktop approval.
export function desktopLines(approval: Approval): string[] {
  const d = approval.desktop;
  if (!d) return [];
  return [`Action: ${d.action}`, `Target: ${d.target}`, ...(typeof d.text === 'string' ? [`Text: ${d.text}`] : [])];
}
export function ApprovalDialog({approval, decide}: {approval: Approval; decide: (decision: Decision) => Promise<void>}) {
  const [busy, setBusy] = useState(false);
  const [stale, setStale] = useState(false);
  const [error, setError] = useState('');
  async function send(decision: Decision) {
    if (busy || stale) return;
    setBusy(true); setError('');
    try { await decide(decision); } catch (e) { if (e instanceof HttpError && e.status === 409) setStale(true); setError(e instanceof Error ? e.message : 'Decision failed. Please try again.'); }
    finally { setBusy(false); }
  }
  if (!canShow(approval)) return <ModalOverlay isOpen isDismissable={false} isKeyboardDismissDisabled={busy || stale} onOpenChange={open => {if (!open) void send('reject');}}>
    <Modal><Dialog aria-describedby="approval-summary">
      <Heading slot="title">Unsupported approval</Heading>
      <p id="approval-summary">Can't show this approval, rejecting it. Only Reject is available.</p>
      <div className="actions"><Button autoFocus isDisabled={busy || stale} onPress={() => void send('reject')}>Reject</Button></div>
      {busy && <p role="status">Sending decision…</p>}
      {error && <p role="alert">{error}</p>}
      {stale && <Button onPress={() => window.location.reload()}>Disconnect and reset</Button>}
    </Dialog></Modal>
  </ModalOverlay>;
  const desktop = desktopLines(approval);
  return <ModalOverlay isOpen isDismissable={false} isKeyboardDismissDisabled={busy || stale} onOpenChange={open => {if (!open) void send('reject');}}>
    <Modal><Dialog aria-describedby={desktop.length ? 'approval-summary approval-desktop' : 'approval-summary'}>
      <Heading slot="title">{DESKTOP_TITLES[approval.kind] ?? (approval.kind === 'write_files' ? 'Review file changes' : 'Review program run')}</Heading>
      <p id="approval-summary">{approval.summary}</p>
      {desktop.length > 0 && <div id="approval-desktop">{desktop.map((line, i) => <pre key={i}>{line}</pre>)}</div>}
      {approval.files.length > 0 && <ul>{approval.files.map(f => <li key={f.path}>{f.path}: {f.action}, {f.lines_added} lines added, {f.lines_removed} removed.</li>)}</ul>}
      {approval.run && <p>Run {approval.run.entry} with {approval.run.preset}. Timeout: {approval.run.timeout_s} seconds.</p>}
      {approval.findings.length > 0 && <section aria-label="Security findings"><h3>Security findings</h3><ul>{approval.findings.map((f, i) => <li key={i}>{f.severity}: {f.rule} in {f.path}, line {f.line}: {f.message}</li>)}</ul></section>}
      {approval.diff && <details><summary>Read the proposed diff</summary><pre tabIndex={0} aria-label="Proposed diff">{approval.diff}</pre></details>}
      {approval.blocking ? <p role="note">Blocked action. Review the security findings. To override, focus Approve and press Shift+Enter. Enter alone cannot approve. Escape rejects.</p> : <p>Choose a button and press Enter. Escape rejects this proposal.</p>}
      <div className="actions"><Button autoFocus={Boolean(approval.blocking)} isDisabled={busy || stale} onPress={() => void send('reject')}>Reject</Button><Button autoFocus={!approval.blocking} isDisabled={busy || stale} aria-keyshortcuts={approval.blocking ? 'Shift+Enter' : undefined} onKeyDown={event => {
        if (approval.blocking && event.key === 'Enter' && event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey && !event.repeat) { event.preventDefault(); event.stopPropagation(); void send('approve'); }
        else event.continuePropagation();
      }} onPress={() => {if (!approval.blocking) void send('approve');}}>Approve{approval.blocking ? ' blocked action (Shift+Enter)' : ''}</Button></div>
      {busy && <p role="status">Sending decision…</p>}
      {error && <p role="alert">{error}</p>}
      {stale && <Button onPress={() => window.location.reload()}>Disconnect and reset</Button>}
    </Dialog></Modal>
  </ModalOverlay>;
}
