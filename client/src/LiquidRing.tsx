import {useEffect, useRef, useState} from 'react';
import {AgentRing, RING_STATES} from './ringRenderer';
import type {RingTarget} from './ringRenderer';
import type {TaskState} from './contracts';
import type {TalkStatus} from './pushToTalk';

export interface RingInputs {connected: boolean; taskState: TaskState | 'idle'; microphone: TalkStatus['state']; approval: boolean}

/** What the ring shows: a picture of the state the text already announces. */
export function ringTarget({connected, taskState, microphone, approval}: RingInputs): RingTarget {
  if (!connected) return RING_STATES.failed; // dim and still until the core is there
  if (microphone === 'starting' || microphone === 'listening') return RING_STATES.listening;
  if (approval || taskState === 'awaiting_reply') return RING_STATES.attention;
  if (taskState === 'queued' || taskState === 'working') return RING_STATES.working;
  if (taskState === 'running') return RING_STATES.running;
  if (taskState === 'succeeded') return RING_STATES.done;
  if (taskState === 'failed') return RING_STATES.failed;
  return RING_STATES.ready;
}

/** The liquid agent ring. Decorative: hidden from assistive technology. `kick` changes -> a splash. */
export function LiquidRing({inputs, kick}: {inputs: RingInputs; kick: number}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const ring = useRef<AgentRing | null>(null);
  const [fallback, setFallback] = useState(false);
  const target = ringTarget(inputs);
  useEffect(() => {
    try { ring.current = new AgentRing(canvas.current!); } catch { setFallback(true); }
    const motion = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const apply = () => { if (ring.current) ring.current.reduced = Boolean(motion?.matches); };
    apply();
    motion?.addEventListener?.('change', apply);
    return () => { motion?.removeEventListener?.('change', apply); ring.current?.destroy(); ring.current = null; };
  }, []);
  useEffect(() => { ring.current?.setTarget(target); }, [target]);
  useEffect(() => { if (kick) ring.current?.kick(0.6); }, [kick]);
  return <div className="agent-ring" aria-hidden="true">
    {fallback ? <div className="agent-ring-fallback"/> : <canvas ref={canvas}/>}
  </div>;
}
