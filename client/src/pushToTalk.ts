export type VoiceAction = 'start' | 'stop';
export type TalkStatus = {state: 'idle' | 'starting' | 'listening' | 'stopping' | 'error'; message: string; stopUnconfirmed?: boolean};
export interface VoiceControl { listen(action: VoiceAction): Promise<void> }

// Key repeats do not open extra microphones. A release sends stop immediately,
// even when the start response is still pending during the listening earcon.
export function createPushToTalk(core: VoiceControl, notify: (status: TalkStatus) => void, shortcutLabel = 'Ctrl+Alt+Space') {
  let held = false;
  let disposed = false;
  let blocked = false;
  let cycle: Promise<void> | null = null;
  let signalRelease: (() => void) | null = null;
  let failure: string | null = null;
  const show = (state: TalkStatus['state'], message: string) => {if (!disposed) notify({state, message, stopUnconfirmed:blocked});};
  async function runCycle() {
    let startFinished = false;
    let stopEarly = false;
    let releaseSignaled = false;
    const released = new Promise<void>(resolve => { signalRelease = () => {if (releaseSignaled) return; releaseSignaled = true; stopEarly = !startFinished; resolve();}; });
    show('starting', 'Opening microphone. Wait for the listening tone, then speak.');
    const start = core.listen('start').then(() => {
      startFinished = true;
      if (held && !releaseSignaled && !failure && !disposed) show('listening', `Listening. Release ${shortcutLabel} or choose Stop listening.`);
    }).catch(() => {
      startFinished = true;
      failure = 'Microphone could not start. Check the core and microphone, or type your request.';
      held = false;
      signalRelease?.();
    });
    await released;
    show('stopping', 'Stopping microphone. Waiting for the transcript.');
    let stopped = false;
    // Always stop on release; never await start before sending this request.
    const stop = core.listen('stop').then(() => {stopped = true;}).catch(() => {});
    await Promise.all([start, stop]);
    if (stopEarly || !stopped) {
      // A final stop closes a late start before the next hold can begin.
      try {await core.listen('stop'); stopped = true;} catch {stopped = false;}
    }
    signalRelease = null;
    if (!stopped) {
      blocked = true; held = false;
      failure = 'Could not confirm the microphone stopped. Close Artemis to stop its core, then restart through the launcher.';
    }
    if (failure) show('error', failure);
    else show('idle', `Microphone stopped. Hold ${shortcutLabel} or choose Start listening to speak again.`);
  }
  function begin() {
    failure = null;
    cycle = runCycle().finally(() => {
      cycle = null;
      if (held && !disposed && !blocked) begin();
    });
  }
  return {
    press() {
      if (held || disposed || blocked) return;
      held = true;
      if (!cycle) begin();
    },
    release() {
      if (!held) return;
      held = false;
      signalRelease?.();
    },
    fail(message: string) {
      failure = message; held = false;
      signalRelease?.();
      if (!cycle) show('error', message);
    },
    settled(): Promise<void> {return cycle ?? Promise.resolve();},
    async dispose() {
      disposed = true; held = false;
      signalRelease?.();
      await cycle;
    },
  };
}
export type PushToTalk = ReturnType<typeof createPushToTalk>;
