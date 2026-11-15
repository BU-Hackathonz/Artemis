import { useCallback, useEffect, useRef, useState } from 'react'
import type Anthropic from '@anthropic-ai/sdk'
import { AgentRing, RING_STATES } from './agentUi/ringRenderer'
import { runPython, stopPython, type RunResult } from './agentUi/pythonSandbox'
import { canListen, canSpeak, listen, speak, stopSpeaking, type Listening } from './agentUi/browserVoice'
import { liquidCursorStore } from '../cursor/liquidCursorSettings'

// Prototype: the Artemis UI committed to main (client/src/App.tsx, ApprovalDialog.tsx,
// VoiceInput.tsx on origin/main) restyled around the liquid agent ring, as minimal as it can be.
// Same sections, wording, controls and flows as main: header, core connection, your request,
// activity log, voice input, the approval dialog (blocked actions: Shift+Enter), footer.
//
// Three ways to connect, none of which needs the real core:
//   in-memory demo / blocked-action demo   scripted Hello World, like main's demos
//   Claude (live)                          Claude Opus 5.5 through the dev server
//                                          (client/dev/claudeRoute.ts). It answers, and proposes
//                                          file changes and runs through the same approvals.
// Approved writes go into an in-browser project; approved runs execute in a Python sandbox in the
// browser (agentUi/pythonSandbox.ts). Speech and listening use the browser's own engines
// (agentUi/browserVoice.ts). Nothing touches this machine.
//
// Accessibility as on main (Artemis is built for blind developers): labelled controls, a live
// status, focus in the request after connecting, a modal approval dialog that focuses Approve (or
// Reject for a blocked action) and rejects on Escape, a focusable log. The ring is decorative.

type TaskState = 'idle' | 'queued' | 'working' | 'awaiting_reply' | 'awaiting_approval' | 'running' | 'succeeded' | 'failed' | 'canceled'
type Mic = 'idle' | 'listening'
type Finding = { rule: string; severity: string; path: string; line: number; message: string }
type Approval = {
  kind: 'write_files' | 'run_program'
  summary: string
  files: { path: string; action: string; lines_added: number; lines_removed: number }[]
  diff: string | null
  run: { entry: string; preset: string; timeout_s: number } | null
  findings: Finding[]
  blocking?: boolean
}
type LogItem = { id: number; text: string; run?: { exit: number | null; out: string } }
type Msg = Anthropic.Beta.BetaMessageParam
type ToolUse = Anthropic.Beta.BetaToolUseBlock
type Turn = { type: string; text?: string; message?: string; model?: string; stopReason?: string; content?: Anthropic.Beta.BetaContentBlock[]; invalid?: { id: string; raw: string } }

const DEMO_TRANSCRIPT = 'Create a Python program that prints Hello World'
const RUN_TIMEOUT_S = 10
const terminal = (s: TaskState) => s === 'idle' || s === 'succeeded' || s === 'failed' || s === 'canceled'
const lineCount = (s: string) => (s === '' ? 0 : s.replace(/\n$/, '').split('\n').length)

function ringFor(connected: boolean, task: TaskState, mic: Mic, approval: Approval | null) {
  if (!connected) return RING_STATES.failed // dim and still until there's something to talk to
  if (mic === 'listening') return RING_STATES.listening
  if (approval || task === 'awaiting_reply') return RING_STATES.attention
  if (task === 'queued' || task === 'working') return RING_STATES.working
  if (task === 'running') return RING_STATES.running
  if (task === 'succeeded') return RING_STATES.done
  if (task === 'failed') return RING_STATES.failed
  return RING_STATES.ready
}

/** The ring, decorative. kick changes -> a splash. */
function Ring({ target, kick, reduced, className }: { target: ReturnType<typeof ringFor>; kick: number; reduced: boolean; className: string }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const ring = useRef<AgentRing | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    try { ring.current = new AgentRing(canvas.current!) } catch { setFailed(true) }
    return () => { ring.current?.destroy(); ring.current = null }
  }, [])
  useEffect(() => { ring.current?.setTarget(target) }, [target])
  useEffect(() => { if (kick) ring.current?.kick(0.6) }, [kick])
  useEffect(() => { if (ring.current) ring.current.reduced = reduced }, [reduced])
  if (failed) return <div aria-hidden="true" className={`${className} grid place-items-center`}><div className="size-1/2 rounded-full border-[10px] border-violet-400/50" /></div>
  return <canvas ref={canvas} aria-hidden="true" className={className} onPointerDown={() => ring.current?.kick(0.5)} />
}

/** "Ctrl+Alt+Space" -> a matcher for key events (hold-to-talk while the page has focus). */
function parseShortcut(s: string) {
  const parts = s.split('+').map((p) => p.trim().toLowerCase()).filter(Boolean)
  const key = parts.pop() ?? ''
  const mods = { ctrl: parts.includes('ctrl') || parts.includes('control'), alt: parts.includes('alt'), shift: parts.includes('shift'), meta: parts.includes('meta') || parts.includes('super') }
  const valid = key !== '' && (mods.ctrl || mods.alt) && [mods.ctrl, mods.alt, mods.shift, mods.meta].filter(Boolean).length >= 2
    && !(mods.ctrl && mods.alt && !mods.shift && /^[a-z0-9]$/.test(key))
  const isKey = (e: KeyboardEvent) => (key === 'space' ? e.code === 'Space' : e.key.toLowerCase() === key)
  return {
    valid,
    down: (e: KeyboardEvent) => isKey(e) && e.ctrlKey === mods.ctrl && e.altKey === mods.alt && e.shiftKey === mods.shift && e.metaKey === mods.meta,
    up: isKey,
  }
}

// Quiet, minimal controls. Focus stays very visible (keyboard and screen-reader users first).
const focus = 'focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-violet-300'
const ghost = `min-h-11 rounded-full px-4 text-sm text-violet-100/80 hover:bg-white/5 hover:text-white disabled:opacity-35 disabled:hover:bg-transparent ${focus}`
const primary = `min-h-11 rounded-full bg-violet-300 px-5 text-sm font-medium text-[#1a0f2e] hover:bg-violet-200 disabled:bg-white/10 disabled:text-white/35 ${focus}`
const field = 'mt-1 block w-full rounded-2xl bg-white/[0.04] px-4 py-2.5 text-white ring-1 ring-white/10 placeholder:text-violet-100/30 focus:outline-none focus:ring-2 focus:ring-violet-300/70 disabled:opacity-50'
const label = 'block text-sm text-violet-100/70'
const heading = 'text-xs font-medium tracking-[0.2em] text-violet-200/50 uppercase'
const quiet = 'text-sm text-violet-100/50'

export default function ArtemisLiquidUI() {
  // connection
  const [connected, setConnected] = useState<null | { blocked: boolean; live: boolean }>(null)
  const [status, setStatus] = useState('Not connected')
  const [connecting, setConnecting] = useState(false)
  const [address, setAddress] = useState('http://127.0.0.1:8000')
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  // task
  const [task, setTask] = useState<TaskState>('idle')
  const [text, setText] = useState('')
  const [approval, setApproval] = useState<Approval | null>(null)
  const [log, setLog] = useState<LogItem[]>([])
  const [kick, setKick] = useState(0)
  const [reply, setReply] = useState<{ text: string; streaming: boolean; model?: string } | null>(null)
  const [files, setFiles] = useState<Record<string, string>>({})
  // speech and voice
  const [speakReplies, setSpeakReplies] = useState(false)
  const [lastSpeech, setLastSpeech] = useState('')
  const [speaking, setSpeaking] = useState(false)
  const [mic, setMic] = useState<Mic>('idle')
  const [micMessage, setMicMessage] = useState('Microphone is off.')
  const [micError, setMicError] = useState(false)
  const [shortcut, setShortcut] = useState('Ctrl+Alt+Space')
  const [draft, setDraft] = useState('Ctrl+Alt+Space')
  const [shortcutError, setShortcutError] = useState('')
  const [reduced] = useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches)

  const timers = useRef<number[]>([])
  const nextId = useRef(1)
  const input = useRef<HTMLTextAreaElement>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const step = useRef<'write' | 'run'>('write')
  // live conversation
  const history = useRef<Msg[]>([])
  const abort = useRef<AbortController | null>(null)
  const pendingTool = useRef<ToolUse | null>(null)
  const owedResult = useRef<string | null>(null) // a tool call left unanswered by a cancel
  const generation = useRef(0)
  const filesRef = useRef(files)
  useEffect(() => { filesRef.current = files }, [files])
  const listening = useRef<Listening | null>(null)

  // The playground's site-wide water cursor would ripple this page: off here.
  useEffect(() => {
    const was = liquidCursorStore.get().enabled
    liquidCursorStore.set({ enabled: false })
    return () => liquidCursorStore.set({ enabled: was })
  }, [])
  useEffect(() => () => { timers.current.forEach(clearTimeout); abort.current?.abort(); listening.current?.abort(); stopSpeaking(); stopPython() }, [])

  const ready = connected !== null
  const live = connected?.live ?? false
  const active = !terminal(task)
  const say = (t: string, run?: LogItem['run']) => setLog((l) => [...l, { id: nextId.current++, text: t, run }].slice(-500))
  const later = (ms: number, fn: () => void) => { timers.current.push(window.setTimeout(fn, ms)) }
  const clearTimers = () => { timers.current.forEach(clearTimeout); timers.current = [] }
  const announce = (t: string) => { setLastSpeech(t); if (speakReplies) speak(t, setSpeaking) }

  // Approval: modal while a decision is needed; focus returns to the request afterwards.
  useEffect(() => {
    const d = dialog.current
    if (!d) return
    if (approval && !d.open) {
      d.showModal()
      // Focus the intended button (as on main: Approve, or Reject for a blocked action). React's
      // autoFocus doesn't apply to a dialog opened with showModal().
      d.querySelector<HTMLElement>('[data-initial-focus]')?.focus()
    }
    if (!approval && d.open) { d.close(); requestAnimationFrame(() => input.current?.focus()) }
  }, [approval])
  // Focus starts in the request field after connecting (as on main).
  useEffect(() => { if (ready) requestAnimationFrame(() => input.current?.focus()) }, [ready])

  // ---- connection -------------------------------------------------------------------------------

  function connect(mode: false | 'demo' | 'blocked' | 'live') {
    setError('')
    if (!mode) {
      // No core in this prototype: the same message main shows when the core isn't reachable.
      setConnecting(true)
      later(600, () => { setConnecting(false); setError('Artemis core is not reachable. Start the core, check its address and token, then reconnect.') })
      return
    }
    setConnecting(true); setStatus('Connecting event stream…')
    later(500, () => {
      setConnecting(false); setConnected({ blocked: mode === 'blocked', live: mode === 'live' }); setToken('')
      setStatus(mode === 'live' ? 'Claude connected (live)' : 'Demo connected')
      say(canSpeak() ? 'Browser speech is available: Repeat last speech reads the latest result aloud.' : 'Speech is unavailable in this browser. Screen reader announcements enabled.')
    })
  }

  function reset() {
    generation.current++
    abort.current?.abort(); history.current = []; pendingTool.current = null; owedResult.current = null
    listening.current?.abort(); stopSpeaking(); stopPython()
    clearTimers(); setConnected(null); setStatus('Not connected'); setTask('idle'); setApproval(null); setLog([]); setText('')
    setMic('idle'); setError(''); setReply(null); setFiles({}); filesRef.current = {}; setLastSpeech('')
  }

  // ---- runs (both modes) ------------------------------------------------------------------------------

  async function executeRun(entry: string, timeoutS: number, gen: number): Promise<RunResult | null> {
    setTask('running'); say(`Running ${entry}`)
    const r = await runPython(filesRef.current, entry, timeoutS, () => say('Python is ready.'))
    if (gen !== generation.current) return null
    const out = r.error ?? (r.timedOut ? `Stopped after ${timeoutS} seconds.` : [r.stdout, r.stderr].filter(Boolean).join('\n'))
    say(r.timedOut ? `${entry} timed out` : r.error ? `${entry} could not run` : `${entry} finished with exit code ${r.exit}`, { exit: r.exit, out })
    return r
  }

  // ---- the scripted demos ---------------------------------------------------------------------------

  function submitDemo(request: string) {
    clearTimers(); setError('')
    setText(''); step.current = 'write'
    say(`Request received: ${request}`)
    setTask('queued')
    later(500, () => { setTask('working'); say('Reading the project') })
    later(1400, () => say('Planning a Python program that prints Hello World'))
    later(2500, () => {
      setTask('awaiting_approval')
      say('Proposal ready: create hello.py')
      const blocked = connected?.blocked
      setApproval({
        kind: 'write_files', summary: 'Create hello.py, a one-line Python program that prints Hello, World!',
        files: [{ path: 'hello.py', action: 'create', lines_added: blocked ? 2 : 1, lines_removed: 0 }],
        diff: blocked ? '--- /dev/null\n+++ hello.py\n@@ -0,0 +1,2 @@\n+API_KEY = "sk-demo-not-a-real-key"\n+print("Hello, World!")'
          : '--- /dev/null\n+++ hello.py\n@@ -0,0 +1 @@\n+print("Hello, World!")',
        run: null,
        findings: blocked ? [{ rule: 'hardcoded-secret', severity: 'high', path: 'hello.py', line: 1, message: 'Looks like a hard-coded API key (synthetic demo finding).' }] : [],
        blocking: blocked,
      })
    })
  }

  function decideDemo(decision: 'approve' | 'reject') {
    setApproval(null)
    if (decision === 'reject') { clearTimers(); setTask('canceled'); say('Proposal rejected. Nothing was changed.'); return }
    if (step.current === 'write') {
      step.current = 'run'
      setTask('working'); say('Wrote hello.py')
      const content = connected?.blocked ? 'API_KEY = "sk-demo-not-a-real-key"\nprint("Hello, World!")\n' : 'print("Hello, World!")\n'
      filesRef.current = { ...filesRef.current, 'hello.py': content }
      setFiles(filesRef.current)
      later(900, () => {
        setTask('awaiting_approval'); say('Proposal ready: run hello.py')
        setApproval({ kind: 'run_program', summary: 'Run hello.py with Python.', files: [], diff: null, run: { entry: 'hello.py', preset: 'python (in-browser sandbox)', timeout_s: RUN_TIMEOUT_S }, findings: [] })
      })
    } else {
      void executeRun('hello.py', RUN_TIMEOUT_S, generation.current).then((r) => {
        if (!r) return
        const ok = r.exit === 0
        setTask(ok ? 'succeeded' : 'failed'); if (ok) setKick((k) => k + 1)
        announce(ok ? `Hello World program ran successfully. It printed: ${r.stdout}` : 'The program failed.')
      })
    }
  }

  // ---- live: Claude ---------------------------------------------------------------------------------

  async function liveTurn(gen: number, retries = 0): Promise<void> {
    const controller = new AbortController()
    abort.current = controller
    let answer = ''
    try {
      setTask('working')
      const res = await fetch('/api/claude', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: history.current }), signal: controller.signal })
      if (!res.ok || !res.body) throw new Error(`The dev server answered ${res.status}.`)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let turn: Turn | null = null
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const events = buffer.split('\n\n')
        buffer = events.pop() ?? ''
        for (const raw of events) {
          if (!raw.startsWith('data: ')) continue
          const event = JSON.parse(raw.slice(6)) as Turn
          if (event.type === 'text' && event.text) { answer += event.text; setReply({ text: answer, streaming: true }) }
          else if (event.type === 'turn') turn = event
          else if (event.type === 'error') throw new Error(event.message)
        }
      }
      if (gen !== generation.current) return
      if (!turn?.content) throw new Error('The reply ended early. Please try again.')
      history.current.push({ role: 'assistant', content: turn.content })
      if (answer) { setReply({ text: answer, streaming: false, model: turn.model }); say(`Artemis: ${answer}`) }

      const tool = turn.content.find((b): b is ToolUse => b.type === 'tool_use')
      if (turn.invalid && tool) {
        // Don't act on input that didn't validate: tell Claude, and let it try again.
        history.current.push({ role: 'user', content: [{ type: 'tool_result', tool_use_id: turn.invalid.id, is_error: true, content: JSON.stringify({ INVALID_JSON: turn.invalid.raw }) }] })
        if (retries < 2) return liveTurn(gen, retries + 1)
        throw new Error('Claude\'s proposal kept coming through incomplete.')
      }
      if (tool) { proposeFromTool(tool); return }
      setTask('succeeded'); setKick((k) => k + 1)
      if (answer) announce(answer)
    } catch (e) {
      if (controller.signal.aborted || gen !== generation.current) return
      setTask('failed'); setError((e as Error).message); say(`Failed: ${(e as Error).message}`)
      if (answer) setReply({ text: answer, streaming: false })
    } finally {
      if (abort.current === controller) abort.current = null
    }
  }

  function proposeFromTool(tool: ToolUse) {
    const input = tool.input as { summary: string; files?: { path: string; content: string }[]; entry?: string }
    if (tool.name === 'propose_run') {
      const entry = input.entry!.trim()
      if (!(entry in filesRef.current)) {
        history.current.push({ role: 'user', content: [{ type: 'tool_result', tool_use_id: tool.id, is_error: true, content: `There is no file named ${entry} in the project. Files: ${Object.keys(filesRef.current).join(', ') || 'none yet'}.` }] })
        void liveTurn(generation.current); return
      }
      pendingTool.current = tool
      setTask('awaiting_approval'); say(`Proposal ready: run ${entry}`)
      setApproval({ kind: 'run_program', summary: input.summary, files: [], diff: null, run: { entry, preset: 'python (in-browser sandbox)', timeout_s: RUN_TIMEOUT_S }, findings: [] })
      return
    }
    const changes = input.files!.map((f) => ({ path: f.path.replace(/^\.?\//, ''), content: f.content }))
    pendingTool.current = tool
    setTask('awaiting_approval'); say(`Proposal ready: ${changes.map((c) => c.path).join(', ')}`)
    setApproval({
      kind: 'write_files', summary: input.summary,
      files: changes.map(({ path, content }) => {
        const old = filesRef.current[path]
        return { path, action: old === undefined ? 'create' : 'replace', lines_added: lineCount(content), lines_removed: old === undefined ? 0 : lineCount(old) }
      }),
      diff: changes.map(({ path, content }) => {
        const old = filesRef.current[path]
        const minus = old === undefined ? '' : old.replace(/\n$/, '').split('\n').map((l) => `-${l}`).join('\n') + '\n'
        return `--- ${old === undefined ? '/dev/null' : path}\n+++ ${path}\n${minus}${content.replace(/\n$/, '').split('\n').map((l) => `+${l}`).join('\n')}`
      }).join('\n\n'),
      run: null, findings: [],
    })
  }

  async function decideLive(decision: 'approve' | 'reject') {
    const tool = pendingTool.current
    setApproval(null)
    if (!tool) return
    pendingTool.current = null
    const gen = generation.current
    const answerWith = (content: string, isError = false) => {
      history.current.push({ role: 'user', content: [{ type: 'tool_result', tool_use_id: tool.id, content, is_error: isError }] })
      void liveTurn(gen)
    }
    if (decision === 'reject') { say('Proposal rejected. Nothing was changed.'); answerWith('The user rejected this proposal. Nothing was changed.'); return }
    const input = tool.input as { files?: { path: string; content: string }[]; entry?: string }
    if (tool.name === 'propose_file_changes') {
      const changes = input.files!.map((f) => ({ path: f.path.replace(/^\.?\//, ''), content: f.content }))
      filesRef.current = { ...filesRef.current, ...Object.fromEntries(changes.map((c) => [c.path, c.content])) }
      setFiles(filesRef.current)
      say(`Wrote ${changes.map((c) => c.path).join(', ')}`)
      answerWith(`Approved. Wrote ${changes.map((c) => `${c.path} (${lineCount(c.content)} lines)`).join(', ')}.`)
      return
    }
    const r = await executeRun(input.entry!.trim(), RUN_TIMEOUT_S, gen)
    if (!r) return
    answerWith(JSON.stringify(r.error ? { error: r.error } : { exit_code: r.exit, timed_out: r.timedOut, stdout: r.stdout.slice(-4000), stderr: r.stderr.slice(-4000) }), Boolean(r.error))
  }

  // ---- request, cancel ----------------------------------------------------------------------------

  function submit() {
    const request = text.trim()
    if (!ready || !request || active) return
    if (!live) { submitDemo(request); return }
    setText(''); setError(''); setReply(null)
    say(`Request received: ${request}`)
    // A tool call left unanswered by a cancel must be answered before anything else.
    const owed = owedResult.current
    owedResult.current = null
    history.current.push({ role: 'user', content: owed
      ? [{ type: 'tool_result', tool_use_id: owed, content: 'The user canceled before this finished. Nothing more was changed.' }, { type: 'text', text: request }]
      : request })
    setTask('queued')
    void liveTurn(generation.current)
  }

  function cancelTask() {
    generation.current++
    clearTimers(); stopPython()
    if (live) {
      if (abort.current) {
        // the request in flight never got an answer: take it back out of the conversation
        abort.current.abort(); abort.current = null
        const last = history.current.pop()
        const blocks = last && Array.isArray(last.content) ? last.content : []
        const result = blocks.find((b) => b.type === 'tool_result')
        if (result && result.type === 'tool_result') owedResult.current = result.tool_use_id
        setReply((r) => r && { ...r, streaming: false })
      } else if (pendingTool.current) {
        owedResult.current = pendingTool.current.id
        pendingTool.current = null
      } else if (task === 'running') {
        // the run was canceled: its tool call is the last assistant turn
        const lastAssistant = history.current[history.current.length - 1]
        const tool = Array.isArray(lastAssistant?.content) ? lastAssistant.content.find((b) => b.type === 'tool_use') : undefined
        if (tool && tool.type === 'tool_use') owedResult.current = tool.id
      }
    }
    setApproval(null); setTask('canceled'); say('Task canceled')
  }

  const decide = (decision: 'approve' | 'reject') => { if (live) void decideLive(decision); else decideDemo(decision) }

  // ---- voice ------------------------------------------------------------------------------------

  const startListening = useCallback(() => {
    if (mic === 'listening' || !ready || active) return
    setMicError(false)
    const handle = listen((t) => setText(t), (err) => {
      listening.current = null
      setMic('idle')
      if (err) { setMicMessage(err); setMicError(true) } else setMicMessage('Microphone is off.')
      requestAnimationFrame(() => input.current?.focus())
    })
    if (handle) { listening.current = handle; setMic('listening'); setMicMessage('Listening. Speak your request, then stop listening.'); return }
    if (!live) {
      // No recognition in this browser: the demo fills in a sample request.
      setMic('listening'); setMicMessage('Listening (simulated: this browser has no speech recognition).')
      timers.current.push(window.setTimeout(() => { setMic('idle'); setMicMessage('Microphone is off.'); setText(DEMO_TRANSCRIPT); input.current?.focus() }, 2400))
      return
    }
    setMicMessage('Speech recognition isn\'t available in this browser (it needs Chrome or Edge). Type your request instead.'); setMicError(true)
  }, [mic, ready, active, live])

  const stopListening = useCallback(() => {
    if (listening.current) { listening.current.stop(); return } // onEnd tidies up
    if (mic === 'listening') {
      timers.current.forEach(clearTimeout); timers.current = []
      setMic('idle'); setMicMessage('Microphone is off.'); setText(DEMO_TRANSCRIPT); input.current?.focus()
    }
  }, [mic])

  // Hold the voice shortcut while this page has focus (a browser can't register a system-wide one).
  useEffect(() => {
    const sc = parseShortcut(shortcut)
    if (!sc.valid) return
    let held = false
    const down = (e: KeyboardEvent) => { if (!e.repeat && sc.down(e)) { e.preventDefault(); held = true; startListening() } }
    const up = (e: KeyboardEvent) => { if (held && sc.up(e)) { e.preventDefault(); held = false; stopListening() } }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up) }
  }, [shortcut, startListening, stopListening])

  function applyShortcut() {
    const next = draft.trim()
    if (!parseShortcut(next).valid) { setShortcutError('Use Control or Alt plus another modifier and a key, for example Ctrl+Alt+Space. Control+Alt+letter or digit combinations are not allowed.'); return }
    setShortcutError(''); setShortcut(next); say(`Voice shortcut set to ${next}.`)
  }

  const taskLabel = task.split('_').join(' ')
  const lastRun = [...log].reverse().find((l) => l.run)
  const fileNames = Object.keys(files).sort()

  return (
    <div className="fixed inset-0 z-50 overflow-auto bg-[#0b0814] text-[#ece6f7] [color-scheme:dark]"
      style={{ backgroundImage: 'radial-gradient(60rem 36rem at 50% 12rem, #1a1030 0%, transparent 70%)' }}>
      <a href="#request" className={`sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 rounded-full bg-violet-300 px-4 py-2 text-[#1a0f2e] ${focus}`}>Skip to request</a>
      <div className="flex justify-end px-3 pt-3"><a href="#bubble-mockups" className={`inline-flex items-center ${ghost}`}>Exit prototype</a></div>

      <header className="mx-auto flex max-w-2xl flex-col items-center px-6 text-center">
        <Ring target={ringFor(ready, task, mic, approval)} kick={kick} reduced={reduced} className="size-56 cursor-pointer sm:size-64" />
        <p className="-mt-3 text-xs font-medium tracking-[0.3em] text-violet-200/50">ARTEMIS · WEB</p>
        <h1 className="mt-2 text-3xl font-light tracking-tight text-white sm:text-4xl">Build with words.</h1>
        <p className="mt-2 text-violet-100/60">Direct a change, review each action, and hear the result.</p>
      </header>

      <main className="mx-auto max-w-2xl space-y-10 px-6 pt-10 pb-16">
        {/* Core connection */}
        <section aria-labelledby="connection-heading" className="space-y-3">
          <div className="flex items-baseline justify-between gap-4">
            <h2 id="connection-heading" className={heading}>Core connection</h2>
            <p role="status" className="flex items-center gap-2 text-sm text-violet-100/70">
              <span aria-hidden="true" className={`size-1.5 rounded-full ${ready ? 'bg-emerald-400' : 'bg-violet-100/30'}`} />{status}
            </p>
          </div>
          {!ready ? <>
            <form className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); connect(false) }}>
              <div><label htmlFor="address" className={label}>Core address</label>
                <input id="address" type="url" required disabled={connecting} value={address} onChange={(e) => setAddress(e.target.value)} className={field} /></div>
              <div><label htmlFor="token" className={label}>Launch token</label>
                <input id="token" type="password" required autoComplete="off" disabled={connecting} value={token} onChange={(e) => setToken(e.target.value)} className={field} /></div>
              <button disabled={connecting} className={primary}>Connect to core</button>
            </form>
            <p className={quiet}>Launch the core first. Your token stays in memory for this connection.</p>
            <div className="flex flex-wrap gap-1">
              <button type="button" disabled={connecting} className={ghost} onClick={() => connect('demo')}>Try in-memory demo</button>
              <button type="button" disabled={connecting} className={ghost} onClick={() => connect('blocked')}>Try blocked-action demo</button>
              <button type="button" disabled={connecting} className={`${ghost} ring-1 ring-violet-300/40`} onClick={() => connect('live')}>Try with Claude (live)</button>
            </div>
            <p className={quiet}>The blocked-action demo adds a synthetic security finding for testing the approval shortcut. Both demos stay in memory.</p>
            <p className={quiet}>Live: Claude answers through the playground's dev server and proposes changes and runs for your approval. Files stay in this page; programs run in a Python sandbox in the browser.</p>
          </> : <>
            <p className={quiet}>{live
              ? 'Live: Claude Opus 5.5. Approved files stay in this page; approved programs run in a Python sandbox in the browser. No access to this machine.'
              : 'Demo only: synthetic Hello World proposals. Approved runs execute in a Python sandbox in the browser.'}</p>
            <button type="button" className={ghost} onClick={reset}>Disconnect and reset</button>
          </>}
        </section>

        {error && <p role="alert" className="rounded-2xl bg-rose-400/10 px-4 py-3 text-sm text-rose-200">{error}</p>}

        {/* Your request */}
        <section aria-labelledby="request-heading" className="space-y-3">
          <div className="flex items-baseline justify-between gap-4">
            <h2 id="request-heading" className={heading}>Your request</h2>
            <p className="text-sm text-violet-100/70">Task: {taskLabel}</p>
          </div>
          <form onSubmit={(e) => { e.preventDefault(); submit() }}>
            <label htmlFor="request" className={label}>{task === 'awaiting_reply' ? 'Answer the clarification question' : 'What would you like to build?'}</label>
            <p id="request-keys" className="mt-0.5 text-xs text-violet-100/40">Enter sends your request. Shift+Enter adds a new line.</p>
            <div className="mt-2 flex items-end gap-2 rounded-3xl bg-white/[0.04] p-2 ring-1 ring-white/10 focus-within:ring-2 focus-within:ring-violet-300/70">
              <textarea ref={input} id="request" rows={3} required value={text} disabled={!ready} aria-describedby="request-keys"
                placeholder="Create a Python program that prints Hello World" onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form?.requestSubmit() } }}
                className="min-h-11 flex-1 resize-y bg-transparent px-2 py-2 text-white placeholder:text-violet-100/30 focus:outline-none disabled:opacity-50" />
              <button disabled={!ready || !text.trim() || active} className={primary}>{task === 'awaiting_reply' ? 'Send reply' : 'Send request'}</button>
            </div>
          </form>
          <div className="flex flex-wrap items-center gap-1">
            <button type="button" className={ghost} disabled={!ready || !active} onClick={cancelTask}>Cancel task</button>
            <button type="button" className={ghost} disabled={!canSpeak() || !lastSpeech} onClick={() => speak(lastSpeech, setSpeaking)}>Repeat last speech</button>
            <button type="button" className={ghost} disabled={!speaking} onClick={() => { stopSpeaking(); setSpeaking(false) }}>Stop speech</button>
            {canSpeak() && <label className="ml-2 flex min-h-11 items-center gap-2 text-sm text-violet-100/70">
              <input type="checkbox" checked={speakReplies} onChange={(e) => setSpeakReplies(e.target.checked)} className="size-4 accent-violet-300" />
              Read replies aloud
            </label>}
          </div>
          {reply && (
            <figure aria-busy={reply.streaming}>
              <figcaption className="text-xs text-violet-200/50">Artemis{reply.model ? ` · ${reply.model}` : ''}{reply.streaming ? ' · answering…' : ''}</figcaption>
              <p className="mt-1 rounded-2xl bg-white/[0.04] px-4 py-3 whitespace-pre-wrap text-violet-50">{reply.text}</p>
            </figure>
          )}
          {lastRun?.run && (
            <figure>
              <figcaption className="text-xs text-violet-200/50">Exit code: {lastRun.run.exit ?? 'none (stopped)'}</figcaption>
              <pre className="mt-1 overflow-auto rounded-2xl bg-white/[0.04] px-4 py-3 font-mono text-sm text-violet-50">{lastRun.run.out || '(no output)'}</pre>
            </figure>
          )}
        </section>

        {/* Project files (what approved changes wrote) */}
        {fileNames.length > 0 && (
          <section aria-labelledby="files-heading" className="space-y-2">
            <h2 id="files-heading" className={heading}>Project files</h2>
            {fileNames.map((name) => (
              <details key={name} className="rounded-2xl bg-white/[0.03] text-sm">
                <summary className={`cursor-pointer rounded-2xl px-4 py-2.5 font-mono text-violet-100/80 ${focus}`}>{name} · {lineCount(files[name])} lines</summary>
                <pre tabIndex={0} aria-label={`Contents of ${name}`} className={`overflow-auto px-4 pb-3 font-mono text-xs text-violet-50 ${focus}`}>{files[name]}</pre>
              </details>
            ))}
          </section>
        )}

        {/* Activity log */}
        <section aria-labelledby="log-heading" className="space-y-3">
          <div className="flex items-baseline justify-between gap-4">
            <h2 id="log-heading" className={heading}>Activity log</h2>
            <p className="text-xs text-violet-100/40">Latest 500 events. Tab into the log and use arrow keys to scroll.</p>
          </div>
          <div role="log" aria-label="Activity log" aria-live={speakReplies ? 'off' : 'polite'} aria-relevant="additions" tabIndex={0}
            className={`max-h-72 min-h-24 space-y-2 overflow-auto rounded-2xl bg-white/[0.03] p-4 text-sm ${focus}`}>
            {log.length === 0 ? <p className="text-violet-100/35">Events will appear here.</p> : log.map((e) => (
              <article key={e.id}>
                <p className="whitespace-pre-wrap text-violet-50/90">{e.text}</p>
                {e.run && <><p className="text-xs text-violet-100/50">Exit code: {e.run.exit ?? 'none'}</p><pre className="mt-1 font-mono text-xs whitespace-pre-wrap text-violet-200/70">{e.run.out}</pre></>}
              </article>
            ))}
          </div>
        </section>

        {/* Voice input */}
        <section aria-labelledby="voice-heading" className="space-y-3">
          <h2 id="voice-heading" className={heading}>Voice input</h2>
          <p id="voice-instructions" className={quiet}>Hold {shortcut} while this page has focus, speak, and release to stop. The listening button is a toggle: activate once to start and again to stop. It works with screen-reader clicks; the microphone stays on until you stop it.{canListen() ? '' : ' This browser has no speech recognition; use Chrome or Edge, or type your request.'}</p>
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" disabled={!ready || (active && mic !== 'listening')} aria-describedby="voice-instructions"
              onClick={() => (mic === 'listening' ? stopListening() : startListening())}
              className={mic === 'listening' ? primary : `${ghost} ring-1 ring-white/15`}>{mic === 'listening' ? 'Stop listening' : 'Start listening'}</button>
            <p aria-label="Microphone status" role={micError ? 'alert' : undefined} className={`text-sm ${micError ? 'text-rose-200' : 'text-violet-100/70'}`}>{micMessage}</p>
          </div>
          <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); applyShortcut() }}>
            <div className="min-w-56 flex-1"><label htmlFor="voice-shortcut" className={label}>Voice shortcut</label>
              <input id="voice-shortcut" value={draft} maxLength={80} disabled={mic === 'listening'} aria-invalid={Boolean(shortcutError)}
                aria-describedby={shortcutError ? 'shortcut-help shortcut-error' : 'shortcut-help'} onChange={(e) => setDraft(e.target.value)} className={field} /></div>
            <button disabled={mic === 'listening'} className={ghost}>Apply shortcut</button>
          </form>
          <p id="shortcut-help" className="text-xs text-violet-100/40">Applies to this page. Use Control or Alt plus another modifier. Control+Alt+letter or digit combinations are not allowed. The desktop app registers it system-wide.</p>
          {shortcutError && <p id="shortcut-error" role="alert" className="text-sm text-rose-200">{shortcutError}</p>}
        </section>
      </main>

      <footer className="mx-auto max-w-2xl px-6 pb-10 text-center text-xs text-violet-100/40">Writes and runs require approval. Review every proposal before approving.</footer>

      <dialog ref={dialog} aria-labelledby="approval-title" aria-describedby="approval-summary"
        onCancel={(e) => { e.preventDefault(); decide('reject') }}
        className="m-auto w-[min(34rem,calc(100vw-2rem))] rounded-3xl bg-[#140c22] p-6 text-[#ece6f7] ring-1 ring-violet-300/30 backdrop:bg-black/60 backdrop:backdrop-blur-sm">
        {approval && <>
          <h2 id="approval-title" className="text-lg font-medium">{approval.kind === 'write_files' ? 'Review file changes' : 'Review program run'}</h2>
          <p id="approval-summary" className="mt-2 text-violet-100/80">{approval.summary}</p>
          {approval.files.length > 0 && <ul className="mt-3 space-y-1 text-sm text-violet-100/70">{approval.files.map((f) => <li key={f.path}><span className="font-mono">{f.path}</span>: {f.action}, {f.lines_added} lines added, {f.lines_removed} removed.</li>)}</ul>}
          {approval.run && <p className="mt-3 text-sm text-violet-100/70">Run <span className="font-mono">{approval.run.entry}</span> with {approval.run.preset}. Timeout: {approval.run.timeout_s} seconds.</p>}
          {approval.findings.length > 0 && <section aria-label="Security findings" className="mt-4 rounded-2xl bg-rose-400/10 p-4">
            <h3 className="text-sm font-medium text-rose-200">Security findings</h3>
            <ul className="mt-1 space-y-1 text-sm text-rose-100/80">{approval.findings.map((f, i) => <li key={i}>{f.severity}: {f.rule} in {f.path}, line {f.line}: {f.message}</li>)}</ul>
          </section>}
          {approval.diff && <details className="mt-3 text-sm"><summary className={`cursor-pointer rounded py-2 text-violet-200/80 ${focus}`}>Read the proposed diff</summary>
            <pre tabIndex={0} aria-label="Proposed diff" className={`mt-1 max-h-72 overflow-auto rounded-xl bg-black/30 p-3 font-mono text-xs text-violet-50 ${focus}`}>{approval.diff}</pre></details>}
          {approval.blocking
            ? <p role="note" className="mt-4 text-sm text-rose-200/90">Blocked action. Review the security findings. To override, focus Approve and press Shift+Enter. Enter alone cannot approve. Escape rejects.</p>
            : <p className="mt-4 text-xs text-violet-100/40">Choose a button and press Enter. Escape rejects this proposal.</p>}
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" data-initial-focus={approval.blocking ? '' : undefined} className={ghost} onClick={() => decide('reject')}>Reject</button>
            <button type="button" data-initial-focus={approval.blocking ? undefined : ''} aria-keyshortcuts={approval.blocking ? 'Shift+Enter' : undefined}
              onKeyDown={(e) => { if (approval.blocking && e.key === 'Enter') { e.preventDefault(); if (e.shiftKey && !e.repeat) decide('approve') } }}
              onClick={() => { if (!approval.blocking) decide('approve') }}
              className={approval.blocking ? `${ghost} ring-1 ring-rose-300/40 text-rose-100` : primary}>
              Approve{approval.blocking ? ' blocked action (Shift+Enter)' : ''}
            </button>
          </div>
        </>}
      </dialog>
    </div>
  )
}
