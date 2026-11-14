// @vitest-environment jsdom
import {describe, it, expect, vi, beforeEach, afterEach, type Mock} from 'vitest';
import {act} from 'react';
import {createRoot, type Root} from 'react-dom/client';
import {ApprovalDialog, ENTER_GUARD_MS} from '../src/ApprovalDialog';
import type {Approval, Decision} from '../src/contracts';

(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true;
// jsdom has no CSS.escape; react-aria's Modal uses it to look up meta tags.
if (!globalThis.CSS?.escape) Object.defineProperty(globalThis, 'CSS', {configurable: true, value: {escape: (value: string) => value.replace(/[^\w-]/g, c => `\\${c}`)}});

const approval: Approval = {approval_id: 'apr_guard', approval_hash: 'sha256:guard', kind: 'write_files', summary: 'Synthetic proposal', files: [], diff: null, run: null, findings: [], blocking: false};
async function flush() {for (let i = 0; i < 6; i++) await act(async () => {await Promise.resolve();});}
function press(target: Element, key: string) {
  target.dispatchEvent(new KeyboardEvent('keydown', {key, bubbles: true, cancelable: true}));
  target.dispatchEvent(new KeyboardEvent('keyup', {key, bubbles: true, cancelable: true}));
}
const approveButton = () => document.querySelector<HTMLButtonElement>('[data-approval-approve]')!;

describe('Enter guard on background-revealed approvals', () => {
  let root: Root;
  let container: HTMLDivElement;
  let now: number;
  let decide: Mock<(decision: Decision) => Promise<void>>;
  beforeEach(() => {
    now = 1_000_000;
    vi.spyOn(Date, 'now').mockImplementation(() => now);
    decide = vi.fn(async (_decision: Decision) => {});
    container = document.body.appendChild(document.createElement('div'));
    root = createRoot(container);
  });
  afterEach(() => {act(() => root.unmount()); container.remove(); vi.restoreAllMocks();});

  async function render(enterGuardUntil?: number) {
    await act(async () => root.render(<ApprovalDialog approval={approval} enterGuardUntil={enterGuardUntil} decide={decide}/>));
    await flush();
  }

  it('ignores Enter on Approve inside the guard window and approves after it', async () => {
    await render(now + ENTER_GUARD_MS);
    expect(document.activeElement).toBe(approveButton());
    await act(async () => press(approveButton(), 'Enter'));
    await flush();
    expect(decide).not.toHaveBeenCalled();
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();

    now += ENTER_GUARD_MS + 50;
    await act(async () => press(approveButton(), 'Enter'));
    await flush();
    expect(decide).toHaveBeenCalledTimes(1);
    expect(decide).toHaveBeenCalledWith('approve');
  });

  it('keeps Escape immediate inside the guard window', async () => {
    await render(now + ENTER_GUARD_MS);
    await act(async () => press(approveButton(), 'Escape'));
    await flush();
    expect(decide).toHaveBeenCalledWith('reject');
    expect(decide).not.toHaveBeenCalledWith('approve');
  });

  it('a foreground approval (no guard) approves on Enter immediately', async () => {
    await render(undefined);
    expect(document.activeElement).toBe(approveButton());
    await act(async () => press(approveButton(), 'Enter'));
    await flush();
    expect(decide).toHaveBeenCalledWith('approve');
  });
});
