// @vitest-environment jsdom
import {describe, it, expect, vi, beforeEach, afterEach} from 'vitest';
import {act} from 'react';
import {createRoot, type Root} from 'react-dom/client';
import type {Core} from '../src/contracts';

const registerPushToTalk = vi.fn<(shortcut: string, handler: (state: 'Pressed'|'Released') => void) => Promise<() => Promise<void>>>();
vi.mock('../src/platform', () => ({platform: {name: 'Desktop', native: true, registerPushToTalk: (...args: Parameters<typeof registerPushToTalk>) => registerPushToTalk(...args)}}));
const {useVoiceInput} = await import('../src/useVoiceInput');

(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true;

function fakeCore() {
  return {announceReady: vi.fn(async () => {}), listen: vi.fn(async () => {}), reportShortcutFailure: vi.fn(async () => {})} as unknown as Core & {announceReady: ReturnType<typeof vi.fn>; reportShortcutFailure: ReturnType<typeof vi.fn>};
}
let latest: ReturnType<typeof useVoiceInput> | undefined;
function Probe({core, sessionId, voiceReady = true}: {core: Core; sessionId: string | null; voiceReady?: boolean}) {
  latest = useVoiceInput(core, sessionId, true, voiceReady);
  return null;
}
async function flush() {for (let i = 0; i < 12; i++) await act(async () => {await Promise.resolve();});}

describe('useVoiceInput shortcut registration', () => {
  let root: Root;
  beforeEach(() => {registerPushToTalk.mockReset(); latest = undefined; root = createRoot(document.createElement('div'));});
  afterEach(() => {act(() => root.unmount());});

  it('registers push-to-talk once across session id changes', async () => {
    const unregister = vi.fn(async () => {});
    registerPushToTalk.mockResolvedValue(unregister);
    const core = fakeCore();
    for (const sessionId of [null, 'ses_1', 'ses_1', 'ses_2']) {
      await act(async () => root.render(<Probe core={core} sessionId={sessionId}/>));
      await flush();
    }
    expect(core.announceReady.mock.calls).toEqual([['ses_1', 'Control+Shift+Space'], ['ses_2', 'Control+Shift+Space']]);
    expect(registerPushToTalk).toHaveBeenCalledTimes(1);
    expect(registerPushToTalk.mock.calls[0][0]).toBe('Control+Shift+Space');
    expect(unregister).not.toHaveBeenCalled();
    expect(latest?.registered).toBe(true);
    expect(core.reportShortcutFailure).not.toHaveBeenCalled();
  });

  it('reports a failure from before the session id once the id arrives, and not twice', async () => {
    registerPushToTalk.mockRejectedValue(new Error('taken'));
    const core = fakeCore();
    await act(async () => root.render(<Probe core={core} sessionId={null}/>));
    await flush();
    expect(latest?.shortcutErrorKind).toBe('registration');
    expect(core.reportShortcutFailure).not.toHaveBeenCalled();
    await act(async () => root.render(<Probe core={core} sessionId="ses_1"/>));
    await flush();
    expect(core.announceReady.mock.calls).toEqual([['ses_1', undefined]]);
    expect(core.announceReady.mock.invocationCallOrder[0]).toBeLessThan(core.reportShortcutFailure.mock.invocationCallOrder[0]);
    expect(core.reportShortcutFailure.mock.calls).toEqual([['ses_1', 'Control+Shift+Space']]);
    await act(async () => root.render(<Probe core={core} sessionId="ses_1" voiceReady/>));
    await flush();
    expect(core.reportShortcutFailure).toHaveBeenCalledTimes(1);
    expect(latest?.noticeFailed).toBe(false);
    expect(registerPushToTalk).toHaveBeenCalledTimes(1);
  });

  it('reports a failure immediately when the session is known and alerts locally on an exact repeat', async () => {
    registerPushToTalk.mockRejectedValue(new Error('taken'));
    const core = fakeCore();
    await act(async () => root.render(<Probe core={core} sessionId="ses_1"/>));
    await flush();
    expect(core.announceReady.mock.calls).toEqual([['ses_1', undefined]]);
    expect(core.announceReady.mock.invocationCallOrder[0]).toBeLessThan(core.reportShortcutFailure.mock.invocationCallOrder[0]);
    expect(core.reportShortcutFailure.mock.calls).toEqual([['ses_1', 'Control+Shift+Space']]);
    expect(latest?.noticeFailed).toBe(false);
    await act(async () => latest!.apply());
    await flush();
    expect(registerPushToTalk).toHaveBeenCalledTimes(2);
    expect(core.reportShortcutFailure).toHaveBeenCalledTimes(1);
    expect(latest?.noticeFailed).toBe(true);
  });

  it('keeps the local alert when core voice is not ready or the report fails', async () => {
    registerPushToTalk.mockRejectedValue(new Error('taken'));
    const quiet = fakeCore();
    await act(async () => root.render(<Probe core={quiet} sessionId={null} voiceReady={false}/>));
    await act(async () => root.render(<Probe core={quiet} sessionId="ses_1" voiceReady={false}/>));
    await flush();
    expect(quiet.reportShortcutFailure).not.toHaveBeenCalled();
    act(() => root.unmount());
    root = createRoot(document.createElement('div'));
    const failing = fakeCore();
    failing.reportShortcutFailure.mockRejectedValue(new Error('404'));
    await act(async () => root.render(<Probe core={failing} sessionId={null}/>));
    await flush();
    await act(async () => root.render(<Probe core={failing} sessionId="ses_1"/>));
    await flush();
    expect(failing.reportShortcutFailure).toHaveBeenCalledTimes(1);
    expect(latest?.noticeFailed).toBe(true);
  });
});


describe('startup announcement ordering', () => {
  it('keeps controls available and surfaces a failed ready request', async () => {
    registerPushToTalk.mockReset();
    registerPushToTalk.mockResolvedValue(async () => {});
    const core = fakeCore();
    core.announceReady.mockRejectedValue(new Error('offline'));
    const root = createRoot(document.createElement('div'));
    try {
      await act(async () => root.render(<Probe core={core} sessionId="ses_failure"/>));
      await flush();
      expect(latest?.startupError).toContain('Could not announce');
      expect(latest?.registered).toBe(true);
      expect(core.reportShortcutFailure).not.toHaveBeenCalled();
    } finally {act(() => root.unmount());}
  });

  it('waits for ready acknowledgement before reporting a shortcut failure', async () => {
    registerPushToTalk.mockReset();
    registerPushToTalk.mockRejectedValue(new Error('taken'));
    let resolve!: () => void;
    const waiting = new Promise<void>(done => {resolve = done;});
    const core = fakeCore();
    core.announceReady.mockImplementation(() => waiting);
    const root = createRoot(document.createElement('div'));
    try {
      await act(async () => root.render(<Probe core={core} sessionId="ses_order"/>));
      await flush();
      expect(core.announceReady.mock.calls).toEqual([['ses_order', undefined]]);
      expect(core.reportShortcutFailure).not.toHaveBeenCalled();
      await act(async () => {resolve();});
      await flush();
      expect(core.reportShortcutFailure).toHaveBeenCalledTimes(1);
      await act(async () => latest!.apply());
      await flush();
      expect(core.announceReady).toHaveBeenCalledTimes(1);
      expect(latest?.noticeFailed).toBe(true);
    } finally {act(() => root.unmount());}
  });
});
