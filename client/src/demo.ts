import type { Approval, Core, CoreEvent, Decision, Health } from './contracts';

// Entirely in-memory: this demo never writes files, records audio or executes code.
export function createDemoCore({blockedRun = false}: {blockedRun?: boolean} = {}): Core {
  const sid = 'ses_demo';
  let seq = 0;
  let taskId = '';
  let active: Approval | null = null;
  const history: CoreEvent[] = [];
  const listeners = new Set<(event: CoreEvent) => void>();
  function emit(type: string, data: Record<string, unknown>, text: string) {
    const event: CoreEvent = {seq: ++seq, session_id:sid, task_id:taskId || null, at:new Date().toISOString(), type, data, announce: {text, priority:'normal'}};
    history.push(event);
    listeners.forEach(receive => receive(event));
  }
  function propose(kind: Approval['kind']) {
    active = {
      approval_id: `apr_${seq + 1}`, approval_hash: `demo:${seq + 1}`, kind,
      summary: kind === 'write_files' ? 'Demo: create main.py with one line that prints Hello, World!' : blockedRun ? 'Demo: blocked run with a synthetic high-severity finding. No code will execute.' : 'Demo: run main.py with Python. No security findings.',
      blocking: kind === 'run_program' && blockedRun,
      files: kind === 'write_files' ? [{path:'main.py', action:'created', lines_added:1, lines_removed:0}] : [],
      diff: kind === 'write_files' ? '--- /dev/null\n+++ main.py\n@@ -0,0 +1 @@\n+print("Hello, World!")\n' : null,
      run: kind === 'run_program' ? {preset:'python', entry:'main.py', timeout_s:10} : null,
      findings: kind === 'run_program' && blockedRun ? [{rule:'DEMO_HIGH',severity:'high',path:'main.py',line:1,message:'Synthetic finding for keyboard testing only; no scanner was run.'}] : [],
    };
    emit('task.state', {state:'awaiting_approval'}, 'Waiting for your approval.');
    emit('approval.requested', active as unknown as Record<string, unknown>, active.summary);
  }
  return {
    health: async (): Promise<Health> => ({ok:true, version:'demo', voice_ready:false, screen_reader:null}),
    session: async () => sid,
    submit: async (_sid, _text, requestId) => {
      taskId = `tsk_${requestId}`;
      emit('task.state', {state:'working'}, 'Demo: preparing the Hello World proposal.');
      propose('write_files');
      return taskId;
    },
    async decide(approval, decision: Decision) {
      if (!active || approval.approval_id !== active.approval_id || approval.approval_hash !== active.approval_hash) throw new Error('Approval is stale.');
      active = null;
      emit('approval.resolved', {approval_id:approval.approval_id, decision, by:'user'}, decision === 'approve' ? 'Approved.' : 'Rejected.');
      if (decision === 'reject') { emit('task.state', {state:'succeeded'}, 'Demo: proposal rejected; no action taken.'); return; }
      if (approval.kind === 'write_files') {
        emit('file.changed', {op_id:'op_write', path:'main.py', action:'created', lines_added:1, lines_removed:0}, 'Demo: main.py created in memory.');
        propose('run_program');
      } else {
        emit('task.state', {state:'running'}, 'Demo: simulating Python execution.');
        emit('run.result', {op_id:'op_run', preset:'python', exit_code:0, timed_out:false, duration_ms:0, stdout_tail:'Hello, World!\n', stderr_tail:'', truncated:false}, 'Demo result: Hello, World!');
        emit('task.state', {state:'succeeded'}, 'Demo completed. No files were written and no code was run.');
      }
    },
    async cancel() {
      if (active) emit('approval.resolved', {approval_id:active.approval_id, decision:'reject', by:'invalidated'}, 'Pending approval invalidated.');
      active = null;
      emit('task.state', {state:'canceled'}, 'Demo canceled.');
    },
    async reply() { throw new Error('This demo does not ask clarification questions.'); },
    async listen() { throw new Error('The in-memory demo does not record audio. Connect to the real core for voice input.'); },
    async speech() { throw new Error('Spoken output is provided by a connected core. Use your screen reader to read the demo log.'); },
    subscribe(_sid, receive, status) {
      listeners.add(receive);
      history.forEach(receive);
      status('Demo connected');
      return () => { listeners.delete(receive); };
    },
  };
}
