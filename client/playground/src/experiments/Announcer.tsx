import { useState } from 'react'

// Screen readers read new entries in a role="log" region aloud. Test with NVDA or Narrator.
export default function Announcer() {
  const [log, setLog] = useState<string[]>([])
  const [text, setText] = useState('Tests passed: 12 of 12.')

  return (
    <section aria-labelledby="ann-h" className="space-y-4">
      <h2 id="ann-h" className="text-xl font-semibold">Live-region announcer</h2>
      <form
        className="flex gap-2"
        onSubmit={(e) => { e.preventDefault(); if (text.trim()) setLog((l) => [...l, text.trim()]) }}
      >
        <label className="sr-only" htmlFor="ann-text">Message to announce</label>
        <input id="ann-text" value={text} onChange={(e) => setText(e.target.value)}
          className="flex-1 rounded border border-neutral-400 bg-transparent px-3 py-2" />
        <button className="rounded bg-amber-400 px-4 py-2 font-medium text-neutral-950">Announce</button>
      </form>
      <div role="log" aria-live="polite" aria-label="Announcements"
        className="h-48 overflow-y-auto rounded border border-neutral-300 p-3 text-sm dark:border-neutral-700">
        {log.map((m, i) => <p key={i}>{m}</p>)}
      </div>
    </section>
  )
}
