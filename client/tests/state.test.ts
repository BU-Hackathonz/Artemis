import { describe, it, expect } from 'vitest';
import { createDemoCore } from '../src/demo';
import { initialState, reduceEvent, taskLabel } from '../src/state';
import type { Approval, CoreEvent } from '../src/contracts';

describe('client event safety', () => {
  it('ignores replayed sequences and removes canceled approvals', async () => {
    const core = createDemoCore();
    let state = initialState;
    let last: CoreEvent | null = null;
    core.subscribe(await core.session(), event => {last=event; state=reduceEvent(state,event);}, () => {});
    const id = await core.submit('ses_demo','hello','cr_1');
    expect(state.approvals).toHaveLength(1);
    const previous = state;
    expect(reduceEvent(state,last!)).toBe(previous);
    await core.cancel(id);
    expect(state.approvals).toHaveLength(0);
    expect(state.taskState).toBe('canceled');
  });
  it('requires separate approvals before demo run and rejects stale approvals', async () => {
    const core = createDemoCore();
    const events: CoreEvent[] = [];
    core.subscribe(await core.session(), e => events.push(e), () => {});
    await core.submit('ses_demo','hello','cr_1');
    const write = events.find(e => e.type === 'approval.requested')!.data as unknown as Approval;
    expect(events.some(e => e.type === 'run.result')).toBe(false);
    await core.decide(write,'approve');
    expect(events.some(e => e.type === 'run.result')).toBe(false);
    await expect(core.decide(write,'approve')).rejects.toThrow('stale');
    const run = events.filter(e => e.type === 'approval.requested')[1].data as unknown as Approval;
    await core.decide(run,'approve');
    expect(events.find(e => e.type === 'run.result')!.data.stdout_tail).toBe('Hello, World!\n');
  });
  it('rejects a proposal without file or run events', async () => {
    const core = createDemoCore();
    const events: CoreEvent[] = [];
    core.subscribe(await core.session(),e => events.push(e),() => {});
    await core.submit('ses_demo','hello','cr_1');
    await core.decide(events.find(e => e.type === 'approval.requested')!.data as unknown as Approval,'reject');
    expect(events.some(e => ['file.changed','run.result'].includes(e.type))).toBe(false);
  });
  it('does not expose malformed approvals as actions', () => {
    const state = reduceEvent(initialState,{seq:1,session_id:'ses_1',task_id:'tsk_1',at:'',announce:null,type:'approval.requested',data:{approval_id:'apr_1'}});
    expect(state.approvals).toHaveLength(0);
    expect(state.events.at(-1)!.type).toBe('error');
  });
});

describe('task status text', () => {
  it('shows idle until there is a request, then queued before its stream arrives', () => {
    expect(taskLabel(initialState, null)).toBe('idle');
    expect(taskLabel(initialState, 'tsk_first')).toBe('queued');
    const finished = {...initialState, taskId:'tsk_first'};
    expect(taskLabel(finished, null)).toBe('succeeded');
    expect(taskLabel(finished, 'tsk_next')).toBe('queued');
  });
});
