import {useCallback, useEffect, useRef, useState} from 'react';
import type {Core} from './contracts';
import {normalizeVoiceShortcut} from './shortcuts';
import {platform} from './platform';
import {createPushToTalk, type PushToTalk, type TalkStatus} from './pushToTalk';

export function useVoiceInput(core: Core | null, sessionId: string | null, ready: boolean, voiceReady: boolean) {
  const controller = useRef<PushToTalk | null>(null);
  const voiceReadyRef = useRef(voiceReady);
  voiceReadyRef.current = voiceReady;
  const readyRef = useRef(ready);
  readyRef.current = ready;
  // Read through a ref: the session id must not re-run (and re-register) the shortcut effect.
  const sessionRef = useRef(sessionId);
  sessionRef.current = sessionId;
  const failure = useRef<{shortcut: string; isDisposed: () => boolean} | null>(null);
  const sources = useRef(new Set<string>());
  const shortcutDown = useRef(false);
  const [status, setStatus] = useState<TalkStatus>({state:'idle',message:'Voice input connects with the desktop core.'});
  const [shortcut, setShortcut] = useState('Control+Shift+Space');
  const [draft, setDraft] = useState(shortcut);
  const [revision, setRevision] = useState(0);
  const noticeRequests = useRef(new Map<string, Promise<void>>());
  const [shortcutErrorKind, setShortcutErrorKind] = useState<'registration'|'validation'|null>(null);
  const [noticeFailed, setNoticeFailed] = useState(false);
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
  // The core suppresses exact repeats per (session, shortcut); this map sends each one once.
  const reportFailure = useCallback((session: string, failed: string, isDisposed: () => boolean, alertRepeat: boolean) => {
    if (!core) return;
    const noticeKey = JSON.stringify([session, 'shortcut_registration_failed', failed]);
    let notice = noticeRequests.current.get(noticeKey);
    if (notice) {
      if (alertRepeat) setNoticeFailed(true); // An exact repeat is not spoken again; announce locally.
      return;
    }
    notice = core.reportShortcutFailure(session, failed).catch(error => {
      if (noticeRequests.current.get(noticeKey) === notice) noticeRequests.current.delete(noticeKey);
      throw error;
    });
    noticeRequests.current.set(noticeKey, notice);
    void notice.catch(() => {if (!isDisposed()) setNoticeFailed(true);});
  }, [core]);
  const fail = useCallback((message: string) => {
    sources.current.clear();
    controller.current?.fail(message);
  }, []);

  useEffect(() => {
    if (!platform.native || !core) return;
    let disposed = false;
    let unregister: (() => Promise<void>) | undefined;
    const talk = createPushToTalk(core, next => {
      if (next.state === 'idle' || next.state === 'error') sources.current.clear();
      setStatus(next);
    }, shortcut);
    controller.current = talk;
    sources.current.clear(); shortcutDown.current = false;
    setRegistered(false); setShortcutError(''); setShortcutErrorKind(null); setNoticeFailed(false);
    setStatus({state:'idle',message:`Hold ${shortcut} or choose Start listening. Wait for the listening tone, then speak. Release to stop.`});
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
      if (disposed) return;
      setShortcutError(`Could not register ${shortcut}. Another app may use it. Choose another shortcut, use Start listening, or type your request.`);
      setShortcutErrorKind('registration');
      // Without core speech, the local alert is the one announcement source.
      if (!voiceReadyRef.current) return;
      failure.current = {shortcut, isDisposed: () => disposed};
      // Before the session id is known, the session effect below reports it once the id arrives.
      if (sessionRef.current) reportFailure(sessionRef.current, shortcut, () => disposed, true);
    });
    const unload = () => {sources.current.clear(); void talk.dispose(); void unregister?.().catch(() => {});};
    window.addEventListener('beforeunload', unload);
    return () => {
      disposed = true; controller.current = null; failure.current = null;
      window.removeEventListener('beforeunload', unload);
      sources.current.clear();
      void talk.dispose(); void unregister?.().catch(() => {});
    };
    // A new session (reconnect) must not re-register: on Windows an immediate unregister and
    // register of the same hotkey can race, and the registration then fails.
  }, [core, shortcut, revision, setSource]);
  useEffect(() => {
    // Report a current registration failure to a session that did not hear it yet,
    // e.g. when registration failed before the session id arrived.
    const current = failure.current;
    if (!sessionId || !current || current.isDisposed() || !voiceReadyRef.current) return;
    reportFailure(sessionId, current.shortcut, current.isDisposed, false);
  }, [sessionId, reportFailure]);
  useEffect(() => {
    if (!ready) {sources.current.clear(); controller.current?.release();}
  }, [ready]);

  const active = ['starting','listening','stopping'].includes(status.state);
  function apply() {
    if (active || status.stopUnconfirmed) return;
    let value: string;
    try {value = normalizeVoiceShortcut(draft.trim());}
    catch (error) {setShortcutErrorKind('validation'); setShortcutError(error instanceof Error ? error.message : 'Choose another voice shortcut.'); return;}
    setDraft(value); setShortcut(value); setRevision(n=>n+1);
  }
  function toggleButton() {
    if (['starting','listening'].includes(status.state) || sources.current.has('button')) {
      sources.current.clear(); controller.current?.release();
    } else if (status.state !== 'stopping' && !status.stopUnconfirmed) setSource('button',true);
  }
  return {toggleButton, status, shortcut, draft, setDraft, apply, shortcutError, shortcutErrorKind, noticeFailed, registered, active, setSource, fail};
}
export type VoiceInputState = ReturnType<typeof useVoiceInput>;
