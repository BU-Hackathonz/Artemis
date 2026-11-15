import { useState, useSyncExternalStore } from 'react'
import { canRefract } from '../cursor/dropletMap'
import { BALL_LENS_PRESET, DROPLET_PRESET, WATER_PRESET, liquidCursorStore, type LiquidCursorSettings } from '../cursor/liquidCursorSettings'

// Test bench for the site-wide liquid-glass cursor: ordinary DOM content to refract, plus
// the interactions that must keep working underneath it, and live tuning.

const SLIDERS: [keyof LiquidCursorSettings, string, number, number, number][] = [
  ['size', 'Size (px)', 16, 200, 1],
  ['refraction', 'Refraction', 0, 2, 0.05],
  ['stretch', 'Max stretch', 0, 1.2, 0.05],
  ['stiffness', 'Stiffness', 80, 1200, 10],
  ['damping', 'Damping', 4, 60, 1],
  ['dispersion', 'Colour split', 0, 0.2, 0.01],
]

export default function DropletBench() {
  const s = useSyncExternalStore(liquidCursorStore.subscribe, liquidCursorStore.get)
  const [clicks, setClicks] = useState(0)
  const [dropped, setDropped] = useState(false)
  const refracts = canRefract()

  return (
    <section aria-labelledby="db-h" className="space-y-6">
      <div>
        <h2 id="db-h" className="text-xl font-semibold">Droplet test bench</h2>
        <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
          The liquid-glass cursor runs across the whole playground. Move slowly over the lines and small text below.
          {!refracts && ' This browser can show the droplet but not refract through it: that needs Chromium (Chrome, Edge, or Tauri’s WebView2).'}
        </p>
      </div>

      <fieldset className="grid gap-3 rounded-xl border border-neutral-300 p-4 sm:grid-cols-2 dark:border-neutral-700">
        <legend className="px-1 text-sm font-medium">Cursor settings</legend>
        <div role="group" aria-label="Preset" className="flex flex-wrap gap-2 sm:col-span-2">
          {([['Water (whole site)', WATER_PRESET], ['Droplet (30px, replaces arrow)', DROPLET_PRESET], ['Ball lens (Vercel-logo shot)', BALL_LENS_PRESET]] as const).map(([label, preset]) => (
            <button key={label} type="button" aria-pressed={s.variant === preset.variant}
              onClick={() => liquidCursorStore.set(preset)}
              className="rounded-full border border-neutral-400 px-3 py-1 text-sm aria-pressed:bg-neutral-900 aria-pressed:text-white dark:aria-pressed:bg-white dark:aria-pressed:text-neutral-900">
              {label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-sm sm:col-span-2">
          <input type="checkbox" checked={s.enabled} onChange={(e) => liquidCursorStore.set({ enabled: e.target.checked })} />
          Liquid-glass cursor on
        </label>
        {SLIDERS.map(([key, label, min, max, step]) => (
          <label key={key} className="flex flex-col gap-1 text-sm">
            <span className="flex justify-between"><span>{label}</span><output>{s[key] as number}</output></span>
            <input type="range" min={min} max={max} step={step} value={s[key] as number}
              onChange={(e) => liquidCursorStore.set({ [key]: Number(e.target.value) })} />
          </label>
        ))}
        <button type="button" onClick={() => liquidCursorStore.set(s.variant === 'ball' ? BALL_LENS_PRESET : s.variant === 'water' ? WATER_PRESET : DROPLET_PRESET)}
          className="justify-self-start rounded-full border border-neutral-400 px-3 py-1 text-sm">
          Reset this preset
        </button>
      </fieldset>

      <div data-testid="wordmark" className="grid h-56 place-items-center rounded-xl bg-white">
        <p className="flex items-center gap-3 text-[64px] font-bold tracking-tight text-black">
          <svg viewBox="0 0 24 21" className="h-14" aria-hidden><path d="M12 0 24 21H0z" /></svg>
          Artemis
        </p>
      </div>

      <div data-testid="lines" className="space-y-4">
        <hr className="border-t border-neutral-900 dark:border-neutral-100" />
        <p className="text-[11px] leading-snug text-neutral-700 dark:text-neutral-300">
          Small text, 11px: the quick brown fox jumps over the lazy dog. 0123456789 — {'{ }'} [ ] ( ) =&gt; Artemis reads your code aloud.
        </p>
        <div className="h-24 rounded-lg"
          style={{ backgroundImage: 'linear-gradient(currentColor 1px, transparent 1px), linear-gradient(90deg, currentColor 1px, transparent 1px)', backgroundSize: '16px 16px', color: 'rgb(120 120 120 / .5)' }} />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <img src="/droplet-test.png" alt="Colour test pattern with lines and numbers" width={480} height={270} className="w-full rounded-lg" />
        <div className="space-y-3 rounded-lg bg-neutral-900 p-4 text-neutral-100">
          <p className="text-sm">Dark panel: the rim should stay visible here too.</p>
          <p className="font-mono text-xs">def greet(name): return f"Hello, {'{name}'}"</p>
          <hr className="border-neutral-500" />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-4">
        <a href="#droplet-bench" data-testid="link"
          onClick={(e) => { e.preventDefault(); setClicks((c) => c + 1) }}
          className="text-blue-700 underline dark:text-blue-300">
          A link (clicked <span data-testid="clicks">{clicks}</span> times)
        </a>
        <button type="button" onClick={() => setClicks((c) => c + 1)} className="rounded-lg bg-amber-400 px-3 py-1.5 text-neutral-950">A button</button>
        <input data-testid="input" placeholder="Native cursor here" className="rounded-lg border border-neutral-400 bg-transparent px-3 py-1.5" />
      </div>

      <div className="flex flex-wrap items-center gap-4">
        <span draggable data-testid="drag" onDragStart={(e) => e.dataTransfer.setData('text/plain', 'drop')}
          className="rounded-lg bg-emerald-300 px-3 py-1.5 text-neutral-950">Drag me</span>
        <div data-testid="dropzone" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); setDropped(true) }}
          className="rounded-lg border-2 border-dashed border-neutral-400 px-6 py-3 text-sm">
          {dropped ? 'Dropped!' : 'Drop here'}
        </div>
      </div>

      <div data-testid="scroller" tabIndex={0} className="h-32 overflow-y-auto rounded-lg border border-neutral-300 p-3 text-sm dark:border-neutral-700">
        {Array.from({ length: 30 }, (_, i) => <p key={i}>Scrollable line {i + 1}: select me, scroll me.</p>)}
      </div>
    </section>
  )
}
