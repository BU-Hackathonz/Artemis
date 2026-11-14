import {Button} from 'react-aria-components';
import {platform} from './platform';
import type {VoiceInputState} from './useVoiceInput';

export function VoiceInput({voice, ready, voiceReady}: {voice: VoiceInputState; ready: boolean; voiceReady: boolean}) {
  return <section aria-labelledby="voice-heading"><h2 id="voice-heading">Voice input</h2>
    {platform.native ? <>
      <p id="voice-instructions">Hold {voice.shortcut}, wait for the listening tone, and release to stop. The listening button is a toggle: activate once to start and again to stop. It works with screen-reader clicks; the microphone stays on until you stop it. Pressing and releasing {voice.shortcut} also stops listening started with the button.</p>
      <Button isDisabled={!ready || voice.status.stopUnconfirmed} aria-busy={voice.status.state==='stopping'} aria-describedby="voice-instructions"
        onPress={event=>{if (!event.ctrlKey && !event.altKey && !event.metaKey) voice.toggleButton();}}>{voice.status.state==='starting' || voice.status.state==='listening' ? 'Stop listening' : voice.status.state==='stopping' ? 'Stopping…' : 'Start listening'}</Button>
      <p aria-label="Microphone status" role={voice.status.state==='error'?'alert':undefined} aria-live={voice.status.state==='error'?'assertive':voiceReady?'off':'polite'}>{voice.status.message}</p>
      {voice.startupError && <p role="status">{voice.startupError}</p>}
      {voice.shortcutError && <p id="shortcut-error" role={voice.shortcutErrorKind==='validation' || voice.noticeFailed || !voiceReady ? 'alert' : undefined}>{voice.shortcutError}</p>}
      <form onSubmit={event=>{event.preventDefault();voice.apply();}}>
        <label htmlFor="voice-shortcut">Global voice shortcut</label>
        <input id="voice-shortcut" value={voice.draft} onChange={event=>voice.setDraft(event.target.value)} maxLength={80} disabled={voice.active || voice.status.stopUnconfirmed} aria-invalid={Boolean(voice.shortcutError)} aria-describedby={voice.shortcutError ? 'shortcut-help shortcut-error' : 'shortcut-help'}/>
        <p id="shortcut-help">Applies to this app launch. Use Control or Alt plus another modifier. System shortcuts and Control+Alt+letter or digit combinations are not allowed.</p>
        <button disabled={voice.active || voice.status.stopUnconfirmed}>Apply shortcut</button>
      </form>
      <p>{voice.registered?'Global shortcut is registered.':'Global shortcut is not registered; the listening button and typed requests remain available.'}</p>
    </> : <p>Use the desktop app for the global push-to-talk shortcut. Typed requests are available here.</p>}
  </section>;
}
