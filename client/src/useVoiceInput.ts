import {useCallback, useEffect, useRef, useState} from 'react';
import type {Core} from './contracts';
import {platform} from './platform';
import {createPushToTalk, type PushToTalk, type TalkStatus} from './pushToTalk';

export function useVoiceInput(core: Core | null, ready: boolean) {
  const controller = useRef<PushToTalk | null>(null);
  const readyRef = useRef(ready);
  readyRef.current = ready;
  const sources = useRef(new Set<string>());
  const shortcutDown = useRef(false);
  const [status, setStatus] = useState<TalkStatus>({state:'idle',message:'Voice input connects with the desktop core.'});
  const [shortcut, setShortcut] = useState('Control+Alt+Space');
  const [draft, setDraft] = useState(shortcut);
  const [revision, setRevision] = useState(0);
  const [shortcutError, setShortcutError] = useState('');
  const [registered, setRegistered] = useState(false);

  const setSource = useCallback((source: string, down: boolean) => {
    if (down) {
      if (!readyRef.current) {setStatus({state:'error',message:'Core is disconnected. Reconnect and try again.'}); return;}
      sources.current.add(source);
      controller.current?.press();
    } else {
      const wasHeld = sources.current.delete(source);
      if (wasHeld || source === 'shortcut') {sources.current.clear(); controller.current?.release();}
    }
  }, []);
  const fail = useCallback((message: string) => {
    sources.current.clear();
    controller.current?.fail(message);
  }, []);

  useEffect(() => {
    if (!platform.native || !core) return;
    let disposed = false;
    let unregister: (() => Promise<void>) | undefined;
    const talk = createPushToTalk(core, setStatus, shortcut);
    controller.current = talk;
    sources.current.clear(); shortcutDown.current = false;
    setRegistered(false); setShortcutError('');
    setStatus({state:'idle',message:`Hold ${shortcut} or the talk button. Wait for the listening tone, then speak. Release to stop.`});
    void platform.registerPushToTalk(shortcut, state => {
      if (state === 'Released') {shortcutDown.current = false; setSource('shortcut',false); return;}
      if (shortcutDown.current) return;
      shortcutDown.current = true;
      setSource('shortcut',true);
    }).then(cleanup => {
      if (disposed) {void cleanup().catch(() => {}); return;}
      unregister = cleanup;
      setRegistered(true);
    }).catch(() => {
      if (!disposed) setShortcutError(`Could not register ${shortcut}. Another app may use it. Choose another shortcut, use the talk button, or type your request.`);
    });
    const unload = () => {sources.current.clear(); void talk.dispose(); void unregister?.().catch(() => {});};
    window.addEventListener('beforeunload', unload);
    return () => {
      disposed = true; controller.current = null;
      window.removeEventListener('beforeunload', unload);
      sources.current.clear();
      void talk.dispose(); void unregister?.().catch(() => {});
    };
  }, [core, shortcut, revision, setSource]);
  useEffect(() => {
    if (!ready) {sources.current.clear(); controller.current?.release();}
  }, [ready]);

  const active = ['starting','listening','stopping'].includes(status.state);
  function apply() {
    if (active || status.stopUnconfirmed) return;
    const value = draft.trim();
    if (!value || value.length > 80 || !/(?:Control|Ctrl|Alt|Shift|Meta|Super|Command)/i.test(value)) {
      setShortcutError('Choose a shortcut with a modifier such as Control+Alt+Space.'); return;
    }
    setDraft(value); setShortcut(value); setRevision(n=>n+1);
  }
  return {status, shortcut, draft, setDraft, apply, shortcutError, registered, active, setSource, fail};
}
export type VoiceInputState = ReturnType<typeof useVoiceInput>;
