import {Button} from 'react-aria-components';
import {platform} from './platform';
import type {VoiceInputState} from './useVoiceInput';

export function VoiceInput({voice, ready, voiceReady}: {voice: VoiceInputState; ready: boolean; voiceReady: boolean}) {
  return <section aria-labelledby="voice-heading"><h2 id="voice-heading">Voice input</h2>
    {platform.native ? <>
      <p id="voice-instructions">Hold the talk button with Space or your pointer, or hold {voice.shortcut}. Wait for the listening tone before speaking. Release to stop.</p>
      <Button isDisabled={!ready || voice.status.stopUnconfirmed} aria-describedby="voice-instructions"
        onPressStart={()=>voice.setSource('button',true)} onPressEnd={()=>voice.setSource('button',false)} onBlur={()=>voice.setSource('button',false)}>Hold to talk</Button>
      <p aria-label="Microphone status" role={voice.status.state==='error'?'alert':undefined} aria-live={voice.status.state==='error'?'assertive':voiceReady?'off':'polite'}>{voice.status.message}</p>
      {voice.shortcutError && <p role="alert">{voice.shortcutError}</p>}
      <form onSubmit={event=>{event.preventDefault();voice.apply();}}>
        <label htmlFor="voice-shortcut">Global voice shortcut</label>
        <input id="voice-shortcut" value={voice.draft} onChange={event=>voice.setDraft(event.target.value)} maxLength={80} disabled={voice.active || voice.status.stopUnconfirmed} aria-describedby="shortcut-help"/>
        <p id="shortcut-help">Applies to this app launch. Choose an unused shortcut with Ctrl, Alt or Shift.</p>
        <button disabled={voice.active || voice.status.stopUnconfirmed}>Apply shortcut</button>
      </form>
      <p>{voice.registered?'Global shortcut is registered.':'Global shortcut is not registered; the talk button and typed requests remain available.'}</p>
    </> : <p>Use the desktop app for the global push-to-talk shortcut. Typed requests are available here.</p>}
  </section>;
}
