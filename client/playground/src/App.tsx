import { useEffect, useState } from 'react'
import { experiments } from './experiments'
import LiquidCursor from './cursor/LiquidCursor'

const fromHash = () => experiments.find((e) => `#${e.id}` === location.hash) ?? experiments[0]

export default function App() {
  const [current, setCurrent] = useState(fromHash)
  useEffect(() => {
    const onHash = () => setCurrent(fromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  const { Component } = current

  return (
    <div className="mx-auto flex min-h-screen max-w-5xl flex-col gap-6 p-4 md:flex-row md:p-8">
      <nav aria-label="Experiments" className="md:w-56 md:shrink-0">
        <h1 className="mb-3 text-lg font-bold">Artemis playground</h1>
        <ul className="space-y-1">
          {experiments.map((e) => (
            <li key={e.id}>
              <a href={`#${e.id}`} aria-current={e.id === current.id ? 'page' : undefined}
                className="block rounded px-3 py-2 hover:bg-neutral-100 aria-[current=page]:bg-amber-400 aria-[current=page]:text-neutral-950 dark:hover:bg-neutral-800">
                {e.title}
              </a>
            </li>
          ))}
        </ul>
      </nav>
      <main className="flex-1"><Component key={current.id} /></main>
      <LiquidCursor />
    </div>
  )
}
