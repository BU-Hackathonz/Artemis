import type { Approval, CoreEvent, TaskState } from './contracts';
export interface ViewState {
  seq: number; events: CoreEvent[]; approvals: {approval: Approval; taskId: string | null}[];
  taskId: string | null; taskState: TaskState; transcript: string | null; voiceReady: boolean | null;
}
export const initialState: ViewState = {seq:0, events:[], approvals:[], taskId:null, taskState:'succeeded', transcript:null, voiceReady:null};
function validApproval(value: unknown): value is Approval {
  if (!value || typeof value !== 'object') return false;
  const a = value as Approval;
  return typeof a.approval_id === 'string' && !!a.approval_id && typeof a.approval_hash === 'string' && !!a.approval_hash &&
    (a.blocking === undefined || typeof a.blocking === 'boolean') && ['write_files','run_program'].includes(a.kind) && typeof a.summary === 'string' &&
    Array.isArray(a.files) && a.files.every(f => f && typeof f === 'object' && typeof f.path === 'string' && typeof f.action === 'string' && typeof f.lines_added === 'number' && typeof f.lines_removed === 'number') &&
    (a.diff === null || typeof a.diff === 'string') &&
    (a.run === null || (typeof a.run === 'object' && typeof a.run.entry === 'string' && typeof a.run.preset === 'string' && typeof a.run.timeout_s === 'number')) &&
    Array.isArray(a.findings) && a.findings.every(f => f && typeof f === 'object' && typeof f.rule === 'string' && typeof f.severity === 'string' && typeof f.path === 'string' && typeof f.line === 'number' && typeof f.message === 'string');
}
export function reduceEvent(state: ViewState, event: CoreEvent): ViewState {
  if (event.seq <= state.seq) return state;
  const next = {...state, seq:event.seq, events:[...state.events, event].slice(-500)};
  if (event.type === 'approval.requested') {
    if (!validApproval(event.data)) {
      next.events = [...next.events, {...event, type:'error', announce:{text:'Invalid approval received. No action is available. Reconnect to the core.', priority:'high'}, data:{}}];
    } else if (!state.approvals.some(item => item.approval.approval_id === event.data.approval_id)) {
      next.approvals = [...state.approvals, {approval:event.data, taskId:event.task_id}];
    }
  }
  if (event.type === 'approval.resolved') next.approvals = state.approvals.filter(item => item.approval.approval_id !== event.data.approval_id);
  if (event.type === 'task.state') {
    const value = event.data.state;
    if (typeof value === 'string' && ['queued','working','awaiting_reply','awaiting_approval','running','succeeded','failed','canceled'].includes(value)) {
      next.taskId = event.task_id; next.taskState = value as TaskState;
      if (['succeeded','failed','canceled'].includes(value)) next.approvals = next.approvals.filter(item => item.taskId !== event.task_id);
    }
  }
  if (event.type === 'voice.status' && typeof event.data.voice_ready === 'boolean') next.voiceReady = event.data.voice_ready;
  if (event.type === 'transcript.final' && typeof event.data.text === 'string') next.transcript = event.data.text;
  return next;
}
