// Working v0 from docs/contracts.md. Replace with OpenAPI-generated types when core lands.
// Deliberately outside src/generated/: these are provisional client adapters.
export const TASK_STATES = ['queued','working','awaiting_reply','awaiting_approval','running','succeeded','failed','canceled'] as const;
export type TaskState = typeof TASK_STATES[number];
export function isTaskState(value: unknown): value is TaskState {
  return typeof value === 'string' && TASK_STATES.includes(value as TaskState);
}
export type Decision = 'approve' | 'reject';
export const DESKTOP_KINDS = ['open','read_outside','run_command','type'] as const;
export type ApprovalKind = 'write_files' | 'run_program' | typeof DESKTOP_KINDS[number];
// What a desktop approval will do (contracts §9). The core covers it with approval_hash.
export interface DesktopSpec { action: string; target: string; text?: string }
export interface Approval {
  blocking?: boolean;
  approval_id: string; approval_hash: string; kind: ApprovalKind; summary: string;
  desktop?: DesktopSpec;
  files: { path: string; action: string; lines_added: number; lines_removed: number }[];
  diff: string | null; run: { preset: string; entry: string; timeout_s: number } | null;
  findings: { rule: string; severity: string; path: string; line: number; message: string }[];
}
export interface CoreEvent {
  seq: number; session_id: string; task_id: string | null; at: string;
  announce: { text: string; priority: 'low' | 'normal' | 'high' } | null;
  type: string; data: Record<string, unknown>;
}
export interface Health { ok: boolean; version: string; voice_ready: boolean; screen_reader: 'nvda' | null }
export interface Core {
  health(): Promise<Health>;
  session(): Promise<string>;
  submit(sid: string, text: string, requestId: string): Promise<string>;
  decide(approval: Approval, decision: Decision): Promise<void>;
  cancel(taskId: string): Promise<void>;
  reply(taskId: string, text: string): Promise<void>;
  speech(action: 'repeat' | 'stop'): Promise<void>;
  listen(action: 'start' | 'stop'): Promise<void>;
  announceReady(sid: string, shortcut?: string): Promise<void>;
  reportShortcutFailure(sid: string, shortcut: string): Promise<void>;
  subscribe(sid: string, receive: (event: CoreEvent) => void, status: (message: string) => void): () => void;
}
export const terminal = (state: TaskState) => ['succeeded', 'failed', 'canceled'].includes(state);
