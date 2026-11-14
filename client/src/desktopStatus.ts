import type {TaskState} from './contracts';
export interface CompanionStatus {
  connection: 'starting' | 'ready' | 'disconnected';
  task: TaskState | 'idle';
  microphone: 'idle' | 'starting' | 'listening' | 'stopping' | 'error';
  approval: boolean;
  speech_ready: boolean;
}
export const startingStatus: CompanionStatus = {connection:'starting', task:'idle', microphone:'idle', approval:false, speech_ready:false};
export function companionText(status: CompanionStatus): string {
  if (status.connection !== 'ready') return status.connection === 'starting' ? 'Connecting to Artemis' : 'Connection needs attention';
  if (status.approval) return 'Approval needed · open controls';
  if (status.microphone === 'listening') return 'Listening · release shortcut to finish';
  if (status.microphone === 'starting') return 'Opening microphone';
  if (status.microphone === 'stopping') return 'Finishing voice request';
  if (status.microphone === 'error') return 'Microphone needs attention';
  if (status.task === 'idle') return 'Ready when you are';
  return `Task ${status.task.replaceAll('_', ' ')}`;
}
