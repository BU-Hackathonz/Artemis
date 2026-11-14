import { fetchEventSource } from '@microsoft/fetch-event-source';
import type { Approval, Core, CoreEvent, Decision, Health } from './contracts';

export class HttpError extends Error {
  constructor(readonly status: number, readonly code?: string, readonly taskId?: string) {
    super(code === 'task_active' ? 'Still working on the last request. Wait for it to finish, or use Cancel task.' : code === 'stale_approval' ? 'Approval is stale. Wait for a new proposal.' : `Core returned HTTP ${status}. Check the core and launch token, then reconnect.`);
  }
}
export function createHttpCore(address: string, token: string): Core {
  const url = new URL(address);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new Error('Use a loopback address such as http://127.0.0.1:8000.');
  }
  if (!token.trim()) throw new Error('Enter the launch token provided by the core.');
  const cursors = new Map<string, number>();
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
  async function request<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(url.origin + path, { method: body === undefined ? 'GET' : 'POST', headers, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(10000), keepalive: path === '/voice/listen' && typeof body === 'object' && body !== null && 'action' in body && body.action === 'stop', cache: 'no-store', redirect: 'error' });
    if (!response.ok) {
      if (response.status === 409 && path.endsWith('/tasks')) {
        const body: unknown = await response.json().catch(() => null);
        if (body && typeof body === 'object' && 'error' in body && body.error === 'task_active' && 'task_id' in body && typeof body.task_id === 'string') throw new HttpError(409, 'task_active', body.task_id);
      }
      throw new HttpError(response.status, response.status === 409 && path.startsWith('/approvals/') ? 'stale_approval' : undefined);
    }
    return response.status === 204 ? undefined as T : response.json() as Promise<T>;
  }
  return {
    health: () => request<Health>('/health'),
    session: async () => (await request<{session_id: string}>('/sessions', {})).session_id,
    submit: async (sid, text, requestId) => (await request<{task_id: string}>(`/sessions/${encodeURIComponent(sid)}/tasks`, {text, source: 'keyboard', client_request_id: requestId})).task_id,
    decide: (approval: Approval, decision: Decision) => request(`/approvals/${encodeURIComponent(approval.approval_id)}`, { decision, approval_hash: approval.approval_hash }),
    cancel: tid => request(`/tasks/${encodeURIComponent(tid)}/cancel`, {}),
    reply: (tid, text) => request(`/tasks/${encodeURIComponent(tid)}/reply`, {text, source:'keyboard'}),
    speech: action => request('/speech', {action}),
    listen: action => request('/voice/listen', {action}),
    announceReady: (sid, shortcut) => request(`/sessions/${encodeURIComponent(sid)}/ready`, shortcut ? {shortcut} : {}),
    reportShortcutFailure: (sid, shortcut) => request(`/sessions/${encodeURIComponent(sid)}/diagnostics`, {code:'shortcut_registration_failed',shortcut}),
    subscribe(sid, receive, status) {
      const controller = new AbortController();
      let last = cursors.get(sid) ?? 0;
      void fetchEventSource(url.origin + `/sessions/${encodeURIComponent(sid)}/events`, {
        headers: {Authorization: headers.Authorization, ...(last ? {'last-event-id': String(last)} : {})}, signal: controller.signal, openWhenHidden: true, redirect:'error',
        async onopen(response) {
          if (!response.ok) throw new HttpError(response.status);
          if (!response.headers.get('content-type')?.startsWith('text/event-stream')) throw new Error('Core did not return an event stream.');
          status('Connected');
        },
        onmessage(message) {
          if (!message.data) return;
          const event: CoreEvent = JSON.parse(message.data);
          if (!event || !Number.isSafeInteger(event.seq) || event.seq < 1 || event.session_id !== sid || (event.task_id !== null && typeof event.task_id !== 'string') || typeof event.type !== 'string' || typeof event.at !== 'string' || !event.data || typeof event.data !== 'object' || Array.isArray(event.data) || (event.announce !== null && (!event.announce || typeof event.announce.text !== 'string' || !['low','normal','high'].includes(event.announce.priority)))) throw new Error('Invalid event from core.');
          if (event.seq <= last) return;
          // fetch-event-source sets Last-Event-ID from the SSE id on reconnect.
          if (message.id !== String(event.seq)) throw new Error('Event sequence does not match SSE id.');
          last = event.seq;
          cursors.set(sid, last);
          receive(event);
        },
        onclose() { throw new Error('Event stream closed.'); },
        onerror(error: unknown) {
          if (error instanceof HttpError && [401,403].includes(error.status)) throw error;
          status('Core disconnected. Reconnecting…');
          return 2000;
        },
      }).catch(() => { if (!controller.signal.aborted) status('Connection failed. Check core and launch token, then reconnect.'); });
      return () => controller.abort();
    },
  };
}
