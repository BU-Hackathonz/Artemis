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
  const setup = useRef<{shortcut?: string; failed?: string; isDisposed: () => boolean; notified: Set<string>; reported: Set<string>} | null>(null);
  const [setupVersion, setSetupVersion] = useState(0);
  const readyRequests = useRef(new Map<string, Promise<void>>());
  const [startupError, setStartupError] = useState('');
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
      setup.current = {shortcut, isDisposed: () => disposed, notified:new Set(), reported:new Set()};
      setSetupVersion(n => n + 1);
    }).catch(() => {
      if (disposed) return;
      setShortcutError(`Could not register ${shortcut}. Another app may use it. Choose another shortcut, use Start listening, or type your request.`);
      setShortcutErrorKind('registration');
      setup.current = {failed:shortcut, isDisposed: () => disposed, notified:new Set(), reported:new Set()};
      setSetupVersion(n => n + 1);
    });
    const unload = () => {sources.current.clear(); void talk.dispose(); void unregister?.().catch(() => {});};
    window.addEventListener('beforeunload', unload);
    return () => {
      disposed = true; controller.current = null; setup.current = null;
      window.removeEventListener('beforeunload', unload);
      sources.current.clear();
      void talk.dispose(); void unregister?.().catch(() => {});
    };
    // A new session (reconnect) must not re-register: on Windows an immediate unregister and
    // register of the same hotkey can race, and the registration then fails.
  }, [core, shortcut, revision, setSource]);
  useEffect(() => {
    const current = setup.current;
    if (!core || !sessionId || !ready || !current || current.isDisposed() || current.notified.has(sessionId)) return;
    current.notified.add(sessionId);
    let announcement = readyRequests.current.get(sessionId);
    if (!announcement) {
      announcement = core.announceReady(sessionId, current.shortcut).catch(error => {
        readyRequests.current.delete(sessionId);
        throw error;
      });
      readyRequests.current.set(sessionId, announcement);
    }
    void announcement.then(() => {
      if (!current.isDisposed()) setStartupError('');
    }).catch(() => {
      current.notified.delete(sessionId);
      if (!current.isDisposed()) setStartupError('Could not announce that Artemis is ready. Typed requests remain available; check the core connection.');
    }).finally(() => {
      // Readiness completes before a shortcut diagnostic, so the failure is heard last.
      // A reconnect retries readiness; a failure already reported for this session is not a new alert.
      if (!current.isDisposed() && current.failed && voiceReadyRef.current) {
        const alreadyReported = current.reported.has(sessionId);
        current.reported.add(sessionId);
        reportFailure(sessionId, current.failed, current.isDisposed, !alreadyReported);
      }
    });
  }, [core, sessionId, ready, setupVersion, reportFailure]);
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
  return {toggleButton, status, shortcut, draft, setDraft, apply, shortcutError, shortcutErrorKind, noticeFailed, startupError, registered, active, setSource, fail};
}
export type VoiceInputState = ReturnType<typeof useVoiceInput>;
