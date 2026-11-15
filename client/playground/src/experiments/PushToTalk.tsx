import { useEffect, useState } from 'react'
import { onPushToTalk } from '../platform'

type State = 'idle' | 'listening' | 'thinking'
const label: Record<State, string> = { idle: 'Ready', listening: 'Listening…', thinking: 'Transcribing…' }

// Mocks the listening states; the real audio goes through the core later.
export default function PushToTalk() {
  const [state, setState] = useState<State>('idle')

  useEffect(() => onPushToTalk(
    () => setState('listening'),
    () => setState((s) => {
      if (s !== 'listening') return s
      setTimeout(() => setState('idle'), 1200)
      return 'thinking'
    }),
  ), [])

  return (
    <section aria-labelledby="ptt-h" className="space-y-4">
      <h2 id="ptt-h" className="text-xl font-semibold">Push-to-talk states</h2>
      <p>Hold <kbd className="rounded border px-1">Ctrl</kbd> + <kbd className="rounded border px-1">Space</kbd> to talk.</p>
      <p role="status" className="text-3xl font-bold">
        <span aria-hidden className={`mr-3 inline-block size-4 rounded-full ${
          state === 'listening' ? 'animate-pulse bg-red-500' : state === 'thinking' ? 'bg-amber-400' : 'bg-neutral-400'}`} />
        {label[state]}
      </p>
    </section>
  )
}
