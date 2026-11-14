// @vitest-environment jsdom
import {describe, it, expect, vi, beforeEach, afterEach} from 'vitest';
import {act} from 'react';
import {createRoot, type Root} from 'react-dom/client';
import type {Approval, Core, CoreEvent} from '../src/contracts';

// The native launcher passes compact=true only for infra/start.ps1 -Compact.
const launch = {compact: false, background: false};
const native = {
  getCoreConfig: vi.fn(async () => ({core_url: 'http://127.0.0.1:9', token: 'test-only', compact: launch.compact})),
  updateCompanionStatus: vi.fn(async (status: {approval: boolean}) => launch.background && status.approval),
  revealApproval: vi.fn(async () => {}),
  enterCompact: vi.fn(async () => {}),
  registerPushToTalk: vi.fn(async () => async () => {}),
};
vi.mock('../src/platform', () => ({platform: {name: 'Desktop', native: true, ...native}}));
vi.mock('../src/LiquidRing', () => ({LiquidRing: () => null}));
let core: Core & {decide: ReturnType<typeof vi.fn>; emit: (event: CoreEvent) => void};
vi.mock('../src/api', async importOriginal => ({...await importOriginal<typeof import('../src/api')>(), createHttpCore: () => core}));
const {App} = await import('../src/App');

(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true;
// jsdom has no CSS.escape; react-aria's Modal uses it to look up meta tags.
if (!globalThis.CSS?.escape) Object.defineProperty(globalThis, 'CSS', {configurable: true, value: {escape: (value: string) => value.replace(/[^\w-]/g, c => `\\${c}`)}});

function fakeCore() {
  let receive: ((event: CoreEvent) => void) | undefined;
  return {
    health: async () => ({ok: true, version: 'test', voice_ready: true, screen_reader: null}),
    session: async () => 'ses_compact',
    subscribe: (_sid: string, next: (event: CoreEvent) => void, status: (message: string) => void) => {receive = next; status('Connected'); return () => {};},
    decide: vi.fn(async () => {}),
    announceReady: async () => {}, listen: async () => {}, reportShortcutFailure: async () => {},
    submit: async () => 'tsk_compact', cancel: async () => {}, reply: async () => {}, speech: async () => {},
    emit: (event: CoreEvent) => receive?.(event),
  } as unknown as typeof core;
}
const approval = (blocking = false): Approval => ({approval_id: 'apr_compact', approval_hash: 'sha256:compact', kind: 'write_files', summary: 'Synthetic proposal', files: [], diff: null, run: null, findings: [], blocking});
async function flush() {for (let i = 0; i < 12; i++) await act(async () => {await Promise.resolve();});}
const companionCalls = () => [native.updateCompanionStatus, native.revealApproval, native.enterCompact].reduce((count, call) => count + call.mock.calls.length, 0);
const focusedButton = () => document.activeElement instanceof HTMLButtonElement ? document.activeElement.textContent : null;

describe('compact desktop status is opt-in', () => {
  let root: Root;
  let container: HTMLDivElement;
  async function openApproval(blocking = false) {
    await act(async () => root.render(<App/>));
    await flush();
    await act(async () => core.emit({seq: 1, session_id: 'ses_compact', task_id: 'tsk_compact', at: new Date().toISOString(), type: 'approval.requested', data: approval(blocking) as unknown as Record<string, unknown>, announce: null}));
    await flush();
  }
  beforeEach(() => {
    for (const call of Object.values(native)) call.mockClear();
    core = fakeCore();
    container = document.body.appendChild(document.createElement('div'));
    root = createRoot(container);
  });
  afterEach(() => {act(() => root.unmount()); container.remove(); vi.restoreAllMocks();});

  it('default launch: a non-blocking approval focuses Approve even in the background, with no companion calls', async () => {
    launch.compact = false; launch.background = true;
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    await openApproval();
    await vi.waitFor(() => expect(focusedButton()).toBe('Approve'));
    expect(companionCalls()).toBe(0);
    expect(document.body.textContent).not.toContain('Use compact status');
  });

  it('default launch: a blocking approval still focuses Reject', async () => {
    launch.compact = false; launch.background = false;
    await openApproval(true);
    await vi.waitFor(() => expect(focusedButton()).toBe('Reject'));
    expect(companionCalls()).toBe(0);
  });

  it('compact launch: a background approval focuses Reject before revealing controls', async () => {
    launch.compact = true; launch.background = true;
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    await openApproval();
    await vi.waitFor(() => expect(focusedButton()).toBe('Reject'));
    await vi.waitFor(() => expect(native.revealApproval).toHaveBeenCalledTimes(1));
    expect(native.updateCompanionStatus).toHaveBeenCalled();
    expect(document.body.textContent).toContain('Use compact status');
  });

  it('compact launch: a foreground approval keeps Approve', async () => {
    launch.compact = true; launch.background = false;
    vi.spyOn(document, 'hasFocus').mockReturnValue(true);
    await openApproval();
    await vi.waitFor(() => expect(focusedButton()).toBe('Approve'));
    expect(native.updateCompanionStatus).toHaveBeenCalled();
    expect(native.revealApproval).not.toHaveBeenCalled();
  });
});
