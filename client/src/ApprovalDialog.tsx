import { useState } from 'react';
import { Button, Dialog, Heading, Modal, ModalOverlay } from 'react-aria-components';
import { HttpError } from './api';
import type { Approval, Decision } from './contracts';
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
  return <ModalOverlay isOpen isDismissable={false} isKeyboardDismissDisabled={busy || stale} onOpenChange={open => {if (!open) void send('reject');}}>
    <Modal><Dialog aria-describedby="approval-summary">
      <Heading slot="title">{approval.kind === 'write_files' ? 'Review file changes' : 'Review program run'}</Heading>
      <p id="approval-summary">{approval.summary}</p>
      {approval.files.length > 0 && <ul>{approval.files.map(f => <li key={f.path}>{f.path}: {f.action}, {f.lines_added} lines added, {f.lines_removed} removed.</li>)}</ul>}
      {approval.run && <p>Run {approval.run.entry} with {approval.run.preset}. Timeout: {approval.run.timeout_s} seconds.</p>}
      {approval.findings.length > 0 && <section aria-label="Security findings"><h3>Security findings</h3><ul>{approval.findings.map((f, i) => <li key={i}>{f.severity}: {f.rule} in {f.path}, line {f.line}: {f.message}</li>)}</ul></section>}
      {approval.diff && <details><summary>Read the proposed diff</summary><pre tabIndex={0} aria-label="Proposed diff">{approval.diff}</pre></details>}
      <p>Choose a button and press Enter. Escape rejects this proposal.</p>
      <div className="actions"><Button autoFocus isDisabled={busy || stale} onPress={() => void send('reject')}>Reject</Button><Button isDisabled={busy || stale} onPress={() => void send('approve')}>Approve</Button></div>
      {busy && <p role="status">Sending decision…</p>}
      {error && <p role="alert">{error}</p>}
      {stale && <Button onPress={() => window.location.reload()}>Disconnect and reset</Button>}
    </Dialog></Modal>
  </ModalOverlay>;
}
