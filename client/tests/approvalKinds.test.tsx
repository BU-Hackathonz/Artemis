// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { act } from 'react';
import { ApprovalDialog } from '../src/ApprovalDialog';
import { initialState, reduceEvent } from '../src/state';
import type { Approval, CoreEvent } from '../src/contracts';

(globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true;
// jsdom has no CSS.escape, which react-aria's modal needs.
(globalThis as {CSS?: unknown}).CSS ??= {escape: (s: string) => s};

const roots: ReturnType<typeof createRoot>[] = [];
afterEach(async () => { await act(async () => { roots.splice(0).forEach(r => r.unmount()); }); document.body.innerHTML = ''; });

const base = {approval_id:'apr_1', approval_hash:'sha256:1', summary:'Desktop step', files:[], diff:null, run:null, findings:[]};
const ev = (seq: number, type: string, data: Record<string, unknown>): CoreEvent => ({seq, session_id:'s', task_id:'t', at:'', announce:null, type, data});

async function show(approval: unknown) {
  const decide = vi.fn(async () => {});
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host); roots.push(root);
  await act(async () => { root.render(createElement(ApprovalDialog, {approval: approval as Approval, decide})); });
  const dialog = document.querySelector('[role="dialog"]') as HTMLElement;
  const buttons = () => [...dialog.querySelectorAll('button')].map(b => b.textContent);
  const description = () => dialog.getAttribute('aria-describedby')!.split(' ').map(id => document.getElementById(id)?.textContent ?? '').join(' ');
  return {decide, buttons, dialog, description, unmount: async () => {}};
}

describe('desktop approval dialog', () => {
  it.each([
    ['open', {action:'open_app', target:'VS Code'}],
    ['read_outside', {action:'read_path', target:'~/Documents/plan.md'}],
    ['run_command', {action:'run_command', target:'~/proj', text:'rm -rf build && echo "done"'}],
    ['type', {action:'type_text', target:'focused window', text:'Hello  world\nline 2'}],
  ])('shows %s word for word, also in the description', async (kind, desktop) => {
    const d = await show({...base, kind, desktop});
    const text = d.dialog.textContent!;
    expect(text).toContain(desktop.target);
    expect(d.description()).toContain(desktop.target);
    if ('text' in desktop) { expect(text).toContain(desktop.text); expect(d.description()).toContain(desktop.text); }
    expect(d.buttons()).toEqual(['Reject', 'Approve']);
    await d.unmount();
  });
});

describe('approvals the dialog cannot show', () => {
  it.each([
    ['unknown kind', {...base, kind:'teleport'}],
    ['desktop kind without desktop', {...base, kind:'run_command'}],
    ['desktop kind with a bad desktop', {...base, kind:'type', desktop:{action:'type_text'}}],
  ])('%s: only Reject, never Approve', async (_n, approval) => {
    const d = await show(approval);
    expect(d.dialog.textContent).toMatch(/can't show this approval, rejecting it/i);
    expect(d.buttons()).toEqual(['Reject']);
    await act(async () => { d.dialog.querySelector('button')!.click(); });
    expect(d.decide).toHaveBeenCalledWith('reject');
    await d.unmount();
  });
});

describe('event reducer', () => {
  it('ignores agent.plan, ring.cue, desktop.action and unknown types without changing state', () => {
    let s = initialState;
    ['agent.plan','ring.cue','desktop.action','something.new'].forEach((t, i) => { s = reduceEvent(s, ev(i+1, t, {goal:'x', steps:[], action:'done', op_id:'o', kind:'type', text:'hi'})); });
    expect(s.seq).toBe(4);
    expect(s.approvals).toEqual([]);
    expect(s.taskState).toBe(initialState.taskState);
    expect(s.events.some(e => e.type === 'error')).toBe(false);
  });
  it('keeps desktop and unknown-kind approvals so they can be rejected', () => {
    let s = reduceEvent(initialState, ev(1, 'approval.requested', {...base, kind:'type', desktop:{action:'type_text', target:'w', text:'hi'}}));
    s = reduceEvent(s, ev(2, 'approval.requested', {approval_id:'apr_2', approval_hash:'h', kind:'teleport'}));
    s = reduceEvent(s, ev(3, 'approval.requested', {...base, approval_id:'apr_3', kind:'run_command', desktop:{target:'x'}}));
    expect(s.approvals.map(a => a.approval.approval_id)).toEqual(['apr_1','apr_2','apr_3']);
    expect(s.approvals[0].approval.desktop?.text).toBe('hi');
    expect(s.approvals[2].approval.desktop).toBeUndefined();
  });
  it('still rejects an approval with no id or hash', () => {
    const s = reduceEvent(initialState, ev(1, 'approval.requested', {kind:'type'}));
    expect(s.approvals).toEqual([]);
    expect(s.events.at(-1)!.type).toBe('error');
  });
});
