// The browser's own speech engine (output) and speech recognition (input, Chromium only), for the
// prototype's "Repeat last speech", "Stop speech" and "Start listening". In the real app the core
// speaks (SAPI) and transcribes; this is the stand-in when there's no core.

export function canSpeak() {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

export function speak(text: string, onState?: (speaking: boolean) => void) {
  if (!canSpeak() || !text.trim()) return
  window.speechSynthesis.cancel()
  const u = new SpeechSynthesisUtterance(text)
  u.onstart = () => onState?.(true)
  u.onend = () => onState?.(false)
  u.onerror = () => onState?.(false)
  window.speechSynthesis.speak(u)
}

export function stopSpeaking() {
  if (canSpeak()) window.speechSynthesis.cancel()
}

// Minimal typing for the Web Speech API's recognition side (not in TypeScript's DOM lib).
type RecognitionEvent = { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }
type RecognitionErrorEvent = { error: string }
interface Recognition {
  lang: string; interimResults: boolean; continuous: boolean
  onresult: ((e: RecognitionEvent) => void) | null
  onerror: ((e: RecognitionErrorEvent) => void) | null
  onend: (() => void) | null
  start(): void; stop(): void; abort(): void
}
type RecognitionCtor = new () => Recognition

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

export function canListen() {
  return typeof window !== 'undefined' && recognitionCtor() !== null
}

export type Listening = { stop(): void; abort(): void }

const ERRORS: Record<string, string> = {
  'not-allowed': 'Microphone permission was denied. Allow it in the browser, or type your request.',
  'service-not-allowed': 'Speech recognition isn\'t allowed here. Type your request instead.',
  'no-speech': 'No speech heard. Try again, or type your request.',
  'audio-capture': 'No microphone found. Check the device, or type your request.',
  network: 'Speech recognition needs a network connection. Type your request instead.',
}

/** Listen until stop(); calls onText with the transcript so far, onEnd when finished. */
export function listen(onText: (text: string) => void, onEnd: (error?: string) => void): Listening | null {
  const Ctor = recognitionCtor()
  if (!Ctor) return null
  const r = new Ctor()
  r.lang = navigator.language || 'en-US'
  r.interimResults = true
  r.continuous = true
  let final = ''
  let error: string | undefined
  r.onresult = (e) => {
    let interim = ''
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const res = e.results[i]
      if (res.isFinal) final += res[0].transcript
      else interim += res[0].transcript
    }
    onText((final + interim).trim())
  }
  r.onerror = (e) => { if (e.error !== 'aborted') error = ERRORS[e.error] ?? `Speech recognition stopped (${e.error}).` }
  r.onend = () => onEnd(error)
  r.start()
  return { stop: () => r.stop(), abort: () => r.abort() }
}
