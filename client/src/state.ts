import {isTaskState} from './contracts';
import type { Approval, CoreEvent, DesktopSpec, TaskState } from './contracts';
export interface ViewState {
  seq: number; events: CoreEvent[]; approvals: {approval: Approval; taskId: string | null}[];
  taskId: string | null; taskState: TaskState; transcript: string | null; voiceReady: boolean | null;
}
export const initialState: ViewState = {seq:0, events:[], approvals:[], taskId:null, taskState:'succeeded', transcript:null, voiceReady:null};
const LEGACY_KINDS = ['write_files','run_program'];
function validLegacy(a: Approval): boolean {
  return typeof a.summary === 'string' &&
    Array.isArray(a.files) && a.files.every(f => f && typeof f === 'object' && typeof f.path === 'string' && typeof f.action === 'string' && typeof f.lines_added === 'number' && typeof f.lines_removed === 'number') &&
    (a.diff === null || typeof a.diff === 'string') &&
    (a.run === null || (typeof a.run === 'object' && typeof a.run.entry === 'string' && typeof a.run.preset === 'string' && typeof a.run.timeout_s === 'number')) &&
    Array.isArray(a.findings) && a.findings.every(f => f && typeof f === 'object' && typeof f.rule === 'string' && typeof f.severity === 'string' && typeof f.path === 'string' && typeof f.line === 'number' && typeof f.message === 'string');
}
function validDesktop(d: unknown): d is DesktopSpec {
  if (!d || typeof d !== 'object') return false;
  const x = d as DesktopSpec;
  return typeof x.action === 'string' && typeof x.target === 'string' && (x.text === undefined || x.text === null || typeof x.text === 'string');
}
// Returns the approval to show, or null when it has no id and hash (nothing can be rejected).
// Desktop and unknown kinds are kept so the dialog can show them or offer only Reject.
function readApproval(value: unknown): Approval | null {
  if (!value || typeof value !== 'object') return null;
  const a = value as Approval;
  if (typeof a.approval_id !== 'string' || !a.approval_id || typeof a.approval_hash !== 'string' || !a.approval_hash || typeof a.kind !== 'string') return null;
  if (a.blocking !== undefined && typeof a.blocking !== 'boolean') return null;
  if (LEGACY_KINDS.includes(a.kind)) return validLegacy(a) ? a : null;
  return {...a, summary: typeof a.summary === 'string' ? a.summary : '', files: Array.isArray(a.files) ? a.files : [], findings: Array.isArray(a.findings) ? a.findings : [], diff: typeof a.diff === 'string' ? a.diff : null, run: null, desktop: validDesktop(a.desktop) ? a.desktop : undefined};
}
export function reduceEvent(state: ViewState, event: CoreEvent): ViewState {
  if (event.seq <= state.seq) return state;
  const next = {...state, seq:event.seq, events:[...state.events, event].slice(-500)};
  if (event.type === 'approval.requested') {
    const approval = readApproval(event.data);
    if (!approval) {
      next.events = [...next.events, {...event, type:'error', announce:{text:'Invalid approval received. No action is available. Reconnect to the core.', priority:'high'}, data:{}}];
    } else if (!state.approvals.some(item => item.approval.approval_id === approval.approval_id)) {
      next.approvals = [...state.approvals, {approval, taskId:event.task_id}];
    }
  }
  // Other event types (agent.plan, ring.cue, desktop.action, ...) fall through: kept in the log, no state change.
  if (event.type === 'approval.resolved') next.approvals = state.approvals.filter(item => item.approval.approval_id !== event.data.approval_id);
  if (event.type === 'task.state') {
    const value = event.data.state;
    if (isTaskState(value)) {
      next.taskId = event.task_id; next.taskState = value as TaskState;
      if (['succeeded','failed','canceled'].includes(value)) next.approvals = next.approvals.filter(item => item.taskId !== event.task_id);
    }
  }
  if (event.type === 'voice.status' && typeof event.data.voice_ready === 'boolean') next.voiceReady = event.data.voice_ready;
  if (event.type === 'transcript.final' && typeof event.data.text === 'string') next.transcript = event.data.text;
  return next;
}

export function taskLabel(state: Pick<ViewState, 'taskId' | 'taskState'>, pendingTaskId: string | null): string {
  return pendingTaskId ? 'queued' : state.taskId ? state.taskState.replaceAll('_', ' ') : 'idle';
}
