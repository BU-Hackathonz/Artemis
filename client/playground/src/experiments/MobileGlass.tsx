import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { buildLensMap, supportsSvgBackdrop } from './liquidLens'

// Design study from the style prompt: "mobile app UI, glassmorphism with liquid glass effects, soft
// frosted panels, vibrant highlights, smooth transitions, balanced depth, energetic mood, high
// contrast lighting with subtle reflections, intricate animations" at 16:9.
//
// Everything is laid out on a 1600×900 design canvas scaled to the stage, so the composition holds
// at any width. The tab bar's selection is a real liquid-glass lens (SVG displacement through
// backdrop-filter, Chromium only) that springs between tabs and stretches while it travels.

const W = 1600, H = 900
const TABS = ['Home', 'Runs', 'Voice', 'Settings'] as const
type Tab = (typeof TABS)[number]
// Tab bar is 400 − 2·12 (screen inset) − 2·16 (bar margin) = 344 wide, minus 2·18 padding = 308 for 4 tabs.
const TAB_W = 77, LENS_W = 72, LENS_H = 58

export default function MobileGlass() {
  const stage = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(0.5)
  const [tab, setTab] = useState<Tab>('Home')

  useLayoutEffect(() => {
    const el = stage.current!
    const fit = () => setScale(el.clientWidth / W)
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  return (
    <section aria-labelledby="mg-h" className="space-y-4">
      <style>{css}</style>
      <h2 id="mg-h" className="text-xl font-semibold">Mobile glass UI</h2>
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        Glassmorphism study for an Artemis companion app. The tabs are real: click them or use the arrow keys.
      </p>
      <div ref={stage} className="relative aspect-video overflow-hidden rounded-3xl">
        <div className="mg-canvas absolute top-0 left-0 origin-top-left" style={{ width: W, height: H, transform: `scale(${scale})` }}>
          <Backdrop />
          <NotificationCard />
          <EchoCard />
          <Phone tab={tab} setTab={setTab} />
        </div>
      </div>
    </section>
  )
}

function Backdrop() {
  return (
    <div aria-hidden className="absolute inset-0 overflow-hidden" style={{ background: 'linear-gradient(135deg, #2a0f5c 0%, #6b1fb1 35%, #ff4f8b 70%, #ffb347 100%)' }}>
      <div className="mg-blob" style={{ left: 120, top: -80, width: 560, height: 560, background: '#00e5ff', animationDelay: '0s' }} />
      <div className="mg-blob" style={{ left: 1040, top: 420, width: 620, height: 620, background: '#ff2e93', animationDelay: '-6s' }} />
      <div className="mg-blob" style={{ left: 640, top: 520, width: 480, height: 480, background: '#ffe14d', animationDelay: '-11s' }} />
      <div className="mg-blob" style={{ left: 1180, top: -120, width: 420, height: 420, background: '#7c4dff', animationDelay: '-3s' }} />
      {/* Soft light sweep for the "high contrast lighting with subtle reflections" */}
      <div className="absolute inset-0" style={{ background: 'radial-gradient(70% 60% at 30% 10%, rgba(255,255,255,.25), transparent 60%)' }} />
    </div>
  )
}

function NotificationCard() {
  return (
    <div className="mg-glass mg-float absolute p-6" style={{ left: 130, top: 250, width: 380, borderRadius: 32 }}>
      <div className="flex items-center gap-3">
        <div className="mg-icon grid size-11 place-items-center rounded-2xl text-lg font-bold">A</div>
        <div className="flex-1">
          <p className="text-[15px] font-semibold text-white">Artemis</p>
          <p className="text-[13px] text-white/70">now</p>
        </div>
      </div>
      <p className="mt-4 text-[20px] leading-snug font-semibold text-white">All green. Want to commit?</p>
      <p className="mt-1 text-[15px] text-white/75">12 tests passed in 3.4 s on greet.py</p>
      <div className="mt-5 flex gap-3">
        <span className="mg-pill flex-1 text-center">Not yet</span>
        <span className="mg-pill mg-pill-hot flex-1 text-center">Commit</span>
      </div>
    </div>
  )
}

function EchoCard() {
  return (
    <div className="mg-glass mg-float absolute p-6" style={{ left: 1110, top: 170, width: 340, borderRadius: 32, animationDelay: '-2.5s' }}>
      <p className="text-[15px] font-semibold text-white/80">Echo Check</p>
      <div className="mt-3 flex items-center gap-5">
        <svg viewBox="0 0 100 100" className="size-28 -rotate-90" aria-hidden>
          <circle cx="50" cy="50" r="42" fill="none" stroke="rgba(255,255,255,.18)" strokeWidth="10" />
          <circle className="mg-ring" cx="50" cy="50" r="42" fill="none" stroke="url(#mg-ring-g)" strokeWidth="10" strokeLinecap="round" pathLength={100} />
          <defs><linearGradient id="mg-ring-g"><stop offset="0" stopColor="#7cf7ff" /><stop offset="1" stopColor="#ff7ad9" /></linearGradient></defs>
        </svg>
        <div>
          <p className="text-[44px] leading-none font-bold text-white">98%</p>
          <p className="mt-1 text-[14px] text-white/70">heard as said</p>
        </div>
      </div>
      <div className="mt-5 flex h-10 items-end gap-[5px]" aria-hidden>
        {Array.from({ length: 22 }, (_, i) => (
          <span key={i} className="mg-bar" style={{ animationDelay: `${-i * 0.11}s`, animationDuration: `${0.9 + (i % 5) * 0.17}s` }} />
        ))}
      </div>
    </div>
  )
}

function Phone({ tab, setTab }: { tab: Tab; setTab: (t: Tab) => void }) {
  return (
    <div className="absolute" style={{ left: 600, top: 40, width: 400, height: 820 }}>
      {/* Device: glass body with a bright edge. */}
      <div className="mg-device absolute inset-0" style={{ borderRadius: 64 }} />
      <div className="absolute overflow-hidden" style={{ inset: 12, borderRadius: 54, background: 'linear-gradient(180deg, rgba(24,8,58,.55), rgba(40,10,70,.35))' }}>
        <div className="flex items-center justify-between px-8 pt-5 text-[15px] font-semibold text-white">
          <span>9:41</span>
          <span className="h-7 w-28 rounded-full bg-black/80" />
          <span>100%</span>
        </div>
        <div className="px-6 pt-6">
          <p className="text-[15px] text-white/70">Artemis</p>
          <h3 className="text-[34px] leading-tight font-bold text-white">{tab}</h3>
        </div>
        <div key={tab} className="mg-enter px-5 pt-4">
          {tab === 'Home' && <HomeScreen />}
          {tab === 'Runs' && <RunsScreen />}
          {tab === 'Voice' && <VoiceScreen />}
          {tab === 'Settings' && <SettingsScreen />}
        </div>
        <TabBar tab={tab} setTab={setTab} />
      </div>
    </div>
  )
}

function HomeScreen() {
  return (
    <div className="space-y-4">
      <div className="mg-glass p-5" style={{ borderRadius: 28 }}>
        <div className="flex items-center gap-3">
          <span className="mg-live" />
          <p className="text-[16px] font-semibold text-white">Listening…</p>
        </div>
        <p className="mt-3 text-[19px] leading-snug text-white">“Run the tests and read me the failures.”</p>
        <div className="mt-4 flex h-8 items-center gap-[4px]" aria-hidden>
          {Array.from({ length: 26 }, (_, i) => (
            <span key={i} className="mg-wave" style={{ animationDelay: `${-i * 0.07}s` }} />
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-4">
        {[['Tests', '12/12', '#3cf0a0'], ['Last run', '3.4 s', '#ffd36e']].map(([k, v, c]) => (
          <div key={k} className="mg-glass p-4" style={{ borderRadius: 24 }}>
            <p className="text-[13px] text-white/70">{k}</p>
            <p className="mt-1 text-[26px] font-bold" style={{ color: c }}>{v}</p>
          </div>
        ))}
      </div>
      <div className="flex gap-3">
        {['Explain', 'Run again', 'Commit'].map((x, i) => <span key={x} className={`mg-pill ${i === 2 ? 'mg-pill-hot' : ''}`}>{x}</span>)}
      </div>
    </div>
  )
}

function RunsScreen() {
  const runs: [string, string, boolean][] = [['greet.py', '12 passed', true], ['parser.py', '1 failed', false], ['voice/ptt.py', '8 passed', true], ['agent/loop.py', '21 passed', true]]
  return (
    <ul className="space-y-3">
      {runs.map(([f, r, ok], i) => (
        <li key={f} className="mg-glass mg-stagger flex items-center gap-3 p-4" style={{ borderRadius: 22, animationDelay: `${i * 60}ms` }}>
          <span className="size-3 rounded-full" style={{ background: ok ? '#3cf0a0' : '#ff5a7a', boxShadow: `0 0 12px ${ok ? '#3cf0a0' : '#ff5a7a'}` }} />
          <span className="flex-1 font-mono text-[15px] text-white">{f}</span>
          <span className="text-[14px] text-white/75">{r}</span>
        </li>
      ))}
    </ul>
  )
}

function VoiceScreen() {
  return (
    <div className="grid place-items-center pt-10">
      <div className="relative grid size-56 place-items-center">
        {[0, 1, 2].map((i) => <span key={i} className="mg-ripple" style={{ animationDelay: `${i * 0.8}s` }} />)}
        <div className="mg-orb grid size-36 place-items-center rounded-full">
          <svg viewBox="0 0 24 24" className="size-14 text-white" fill="currentColor" aria-hidden>
            <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2Z" />
          </svg>
        </div>
      </div>
      <p className="mt-6 text-[18px] text-white/85">Hold Ctrl + Space to talk</p>
    </div>
  )
}

function SettingsScreen() {
  const [on, setOn] = useState([true, true, false])
  const rows = ['Read errors aloud', 'Earcons', 'Braille output']
  return (
    <div className="mg-glass divide-y divide-white/15" style={{ borderRadius: 26 }}>
      {rows.map((r, i) => (
        <label key={r} className="flex items-center justify-between px-5 py-4 text-[16px] text-white">
          {r}
          <button type="button" role="switch" aria-checked={on[i]} onClick={() => setOn((s) => s.map((v, j) => (j === i ? !v : v)))}
            className="mg-switch" />
        </label>
      ))}
    </div>
  )
}

const icons: Record<Tab, React.ReactNode> = {
  Home: <path d="M12 3 3 10v10h6v-6h6v6h6V10z" />,
  Runs: <path d="M4 5h16v3H4zm0 5.5h16v3H4zM4 16h16v3H4z" />,
  Voice: <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2Z" />,
  Settings: <path d="M19.4 13a7.6 7.6 0 0 0 0-2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-1.7-1L15 3.5h-4l-.4 2.5a7 7 0 0 0-1.7 1l-2.4-1-2 3.4L6.6 11a7.6 7.6 0 0 0 0 2l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 1.7 1l.3 2.5h4l.4-2.5a7 7 0 0 0 1.7-1l2.4 1 2-3.4zM13 15.5A3.5 3.5 0 1 1 13 8.5a3.5 3.5 0 0 1 0 7z" />,
}

// Tab bar with a liquid-glass lens as the selection. The lens refracts the icons under it, springs
// to the chosen tab, stretches while travelling, and overshoots once before settling.
function TabBar({ tab, setTab }: { tab: Tab; setTab: (t: Tab) => void }) {
  const lens = useRef<HTMLDivElement>(null)
  const svgOk = useMemo(supportsSvgBackdrop, [])
  const map = useMemo(() => buildLensMap(LENS_W, LENS_H, LENS_H / 2, 18, 22), [])
  const index = TABS.indexOf(tab)
  const goal = useRef(index)
  goal.current = index

  useEffect(() => {
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches
    const s = { x: goal.current * TAB_W, v: 0 }
    let last = performance.now(), raf = 0
    const tick = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.05); last = now
      const target = goal.current * TAB_W
      if (reduce) { s.x = target; s.v = 0 }
      for (let t = 0; t < dt; t += 1 / 240) { s.v += (320 * (target - s.x) - 22 * s.v) / 240; s.x += s.v / 240 }
      const stretch = Math.min(Math.abs(s.v) / 900, 0.45)
      lens.current!.style.transform = `translateX(${s.x}px) scale(${1 + stretch}, ${1 - stretch * 0.35})`
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [])

  const onKey = (e: React.KeyboardEvent) => {
    const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    if (!d) return
    e.preventDefault()
    const next = TABS[(index + d + TABS.length) % TABS.length]
    setTab(next)
    ;(e.currentTarget.querySelector(`[data-tab="${next}"]`) as HTMLElement | null)?.focus()
  }

  return (
    <div className="mg-glass absolute" style={{ left: 16, right: 16, bottom: 20, height: 84, borderRadius: 42 }}>
      <svg width="0" height="0" className="absolute" aria-hidden>
        <filter id="mg-tab-lens" x="0" y="0" width={LENS_W} height={LENS_H} filterUnits="userSpaceOnUse" colorInterpolationFilters="sRGB">
          <feImage href={map.url} x="0" y="0" width={LENS_W} height={LENS_H} preserveAspectRatio="none" result="m" />
          <feDisplacementMap in="SourceGraphic" in2="m" scale={map.scale} xChannelSelector="R" yChannelSelector="G" />
        </filter>
      </svg>
      <div role="tablist" aria-label="Sections" onKeyDown={onKey} className="relative flex h-full items-center px-[18px]">
        {TABS.map((t) => (
          <button key={t} type="button" role="tab" data-tab={t} aria-selected={t === tab} tabIndex={t === tab ? 0 : -1}
            onClick={() => setTab(t)}
            className="relative z-0 flex shrink-0 flex-col items-center gap-1 text-[12px] font-semibold transition-colors duration-300"
            style={{ width: TAB_W, color: t === tab ? '#fff' : 'rgba(255,255,255,.62)' }}>
            <svg viewBox="0 0 24 24" className="size-7" fill="currentColor" aria-hidden>{icons[t]}</svg>
            {t}
          </button>
        ))}
        <div ref={lens} aria-hidden className="mg-lens pointer-events-none absolute"
          style={{
            left: 18 + (TAB_W - LENS_W) / 2, top: (84 - LENS_H) / 2, width: LENS_W, height: LENS_H,
            backdropFilter: svgOk ? 'url(#mg-tab-lens) saturate(1.3) brightness(1.08)' : 'blur(4px) brightness(1.15)',
          }} />
      </div>
    </div>
  )
}

const css = `
.mg-canvas { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.mg-blob { position: absolute; border-radius: 50%; filter: blur(90px); opacity: .75; animation: mg-drift 18s ease-in-out infinite alternate; }
@keyframes mg-drift { 0% { transform: translate(0,0) scale(1); } 50% { transform: translate(80px,-60px) scale(1.15); } 100% { transform: translate(-60px,50px) scale(.92); } }
.mg-glass {
  background: linear-gradient(135deg, rgba(255,255,255,.26), rgba(255,255,255,.08));
  backdrop-filter: blur(22px) saturate(1.7);
  border: 1px solid rgba(255,255,255,.38);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.6), inset 0 -1px 0 rgba(255,255,255,.12), 0 20px 50px rgba(30,0,60,.28);
}
.mg-device {
  background: linear-gradient(160deg, rgba(255,255,255,.35), rgba(255,255,255,.06) 40%, rgba(255,255,255,.18));
  backdrop-filter: blur(10px) saturate(1.4);
  border: 1.5px solid rgba(255,255,255,.55);
  box-shadow: inset 0 0 0 1px rgba(255,255,255,.15), 0 40px 90px rgba(20,0,50,.45);
}
.mg-float { animation: mg-float 7s ease-in-out infinite; }
@keyframes mg-float { 50% { transform: translateY(-14px); } }
.mg-icon { color: #fff; background: linear-gradient(135deg, #7cf7ff, #ff6ad5); box-shadow: 0 6px 18px rgba(255,90,200,.45); }
.mg-pill { padding: 10px 18px; border-radius: 999px; font-size: 15px; font-weight: 600; color: #fff;
  background: rgba(255,255,255,.16); border: 1px solid rgba(255,255,255,.35); box-shadow: inset 0 1px 0 rgba(255,255,255,.5); }
.mg-pill-hot { background: linear-gradient(135deg, #ff4f8b, #ff9a3c); border-color: rgba(255,255,255,.5); box-shadow: 0 8px 22px rgba(255,80,140,.5), inset 0 1px 0 rgba(255,255,255,.6); }
.mg-ring { stroke-dasharray: 98 100; animation: mg-ring 2.4s cubic-bezier(.2,.8,.2,1) both; }
@keyframes mg-ring { from { stroke-dasharray: 0 100; } }
.mg-bar { flex: 1; border-radius: 4px; background: linear-gradient(#7cf7ff, #ff7ad9); animation: mg-bar 1s ease-in-out infinite alternate; transform-origin: bottom; }
@keyframes mg-bar { from { height: 18%; } to { height: 100%; } }
.mg-wave { flex: 1; height: 100%; border-radius: 3px; background: rgba(255,255,255,.85); animation: mg-wave 1.1s ease-in-out infinite; transform: scaleY(.2); }
@keyframes mg-wave { 50% { transform: scaleY(1); } }
.mg-live { width: 12px; height: 12px; border-radius: 50%; background: #ff4f8b; box-shadow: 0 0 0 0 rgba(255,79,139,.7); animation: mg-live 1.4s infinite; }
@keyframes mg-live { 70% { box-shadow: 0 0 0 12px rgba(255,79,139,0); } 100% { box-shadow: 0 0 0 0 rgba(255,79,139,0); } }
.mg-enter { animation: mg-enter .45s cubic-bezier(.2,.9,.25,1) both; }
@keyframes mg-enter { from { opacity: 0; transform: translateY(16px) scale(.98); filter: blur(6px); } }
.mg-stagger { animation: mg-enter .5s cubic-bezier(.2,.9,.25,1) both; }
.mg-orb { background: radial-gradient(circle at 35% 30%, #fff 0 6%, #ff8fd8 20%, #8a5bff 60%, #3a1a9e); box-shadow: 0 0 60px rgba(255,120,220,.7), inset 0 -10px 30px rgba(40,0,120,.5); animation: mg-breathe 2.4s ease-in-out infinite; }
@keyframes mg-breathe { 50% { transform: scale(1.06); } }
.mg-ripple { position: absolute; inset: 0; border-radius: 50%; border: 2px solid rgba(255,255,255,.6); animation: mg-ripple 2.4s ease-out infinite; }
@keyframes mg-ripple { from { transform: scale(.6); opacity: 1; } to { transform: scale(1.25); opacity: 0; } }
.mg-switch { width: 52px; height: 32px; border-radius: 999px; position: relative; background: rgba(255,255,255,.2); border: 1px solid rgba(255,255,255,.4); transition: background .3s; }
.mg-switch::after { content: ""; position: absolute; top: 3px; left: 3px; width: 24px; height: 24px; border-radius: 50%; background: #fff; box-shadow: 0 2px 6px rgba(0,0,0,.25); transition: transform .35s cubic-bezier(.3,1.5,.5,1); }
.mg-switch[aria-checked="true"] { background: linear-gradient(135deg, #3cf0a0, #19c3ff); }
.mg-switch[aria-checked="true"]::after { transform: translateX(20px); }
.mg-lens { border-radius: 999px; background: radial-gradient(120% 120% at 30% 15%, rgba(255,255,255,.35), rgba(255,255,255,.06) 60%);
  box-shadow: inset 0 1px 1.5px rgba(255,255,255,.95), inset 0 0 0 1px rgba(255,255,255,.45), 0 6px 16px rgba(30,0,60,.3); z-index: 1; }
@media (prefers-reduced-motion: reduce) {
  .mg-canvas *, .mg-canvas { animation: none !important; transition: none !important; }
  .mg-wave { transform: scaleY(.5); } .mg-bar { height: 60%; }
}
`
