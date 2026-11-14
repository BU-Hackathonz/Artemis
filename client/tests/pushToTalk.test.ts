import {describe, it, expect, vi} from 'vitest';
import {createPushToTalk, type TalkStatus, type VoiceAction} from '../src/pushToTalk';
function deferred() {let resolve!:()=>void; const promise = new Promise<void>(r => {resolve=r;}); return {promise,resolve};}
async function flush() {for(let i=0;i<12;i++) await Promise.resolve();}

describe('push-to-talk lifecycle', () => {
  it('deduplicates key repeats and stops a normal hold once', async () => {
    const listen=vi.fn(async (_action:VoiceAction)=>{});
    const statuses:TalkStatus[]=[];
    const talk=createPushToTalk({listen},status=>statuses.push(status));
    talk.press(); talk.press(); await flush();
    expect(listen.mock.calls).toEqual([['start']]);
    expect(statuses.at(-1)?.state).toBe('listening');
    talk.release(); talk.release(); await talk.settled();
    expect(listen.mock.calls).toEqual([['start'],['stop']]);
    expect(statuses.at(-1)?.state).toBe('idle');
  });
  it('sends stop before a pending start finishes, then closes any late start', async () => {
    const pending=deferred();
    const actions:VoiceAction[]=[];
    const statuses:TalkStatus[]=[];
    const talk=createPushToTalk({listen: action=>{actions.push(action);return action==='start'?pending.promise:Promise.resolve();}},status=>statuses.push(status));
    talk.press(); talk.release(); await flush();
    expect(actions).toEqual(['start','stop']);
    expect(statuses.some(s=>s.state==='listening')).toBe(false);
    pending.resolve(); await talk.settled();
    expect(actions).toEqual(['start','stop','stop']);
    expect(statuses.at(-1)?.state).toBe('idle');
  });
  it('waits for the previous stop barrier before a rapid re-press starts again', async () => {
    const pending=deferred();
    const actions:VoiceAction[]=[];
    const talk=createPushToTalk({listen: action=>{actions.push(action);return actions.length===1?pending.promise:Promise.resolve();}},()=>{});
    talk.press(); talk.release(); talk.press(); await flush();
    expect(actions).toEqual(['start','stop']);
    pending.resolve(); await flush();
    expect(actions).toEqual(['start','stop','stop','start']);
    talk.release(); await talk.settled();
    expect(actions.at(-1)).toBe('stop');
  });
  it('does not start a queued hold if it was released before cleanup finished', async () => {
    const pending=deferred();
    const actions:VoiceAction[]=[];
    const talk=createPushToTalk({listen: action=>{actions.push(action);return actions.length===1?pending.promise:Promise.resolve();}},()=>{});
    talk.press(); talk.release(); talk.press(); talk.release(); pending.resolve(); await talk.settled();
    expect(actions.filter(a=>a==='start')).toHaveLength(1);
  });
  it('cleans up a held microphone and ignores presses after disposal', async () => {
    const listen=vi.fn(async (_action:VoiceAction)=>{});
    const talk=createPushToTalk({listen},()=>{});
    talk.press(); await flush(); await talk.dispose(); talk.press();
    expect(listen.mock.calls).toEqual([['start'],['stop']]);
  });
  it('surfaces start failure and still attempts stop', async () => {
    const statuses:TalkStatus[]=[];
    const listen=vi.fn(async (action:VoiceAction)=>{if(action==='start')throw new Error('offline');});
    const talk=createPushToTalk({listen},s=>statuses.push(s));
    talk.press(); await talk.settled();
    expect(listen.mock.calls).toContainEqual(['stop']);
    expect(statuses.at(-1)?.state).toBe('error');
    expect(statuses.at(-1)?.message).toContain('type your request');
  });
  it('retries stop and blocks more starts if stopping cannot be confirmed', async () => {
    const statuses:TalkStatus[]=[];
    const listen=vi.fn(async (action:VoiceAction)=>{if(action==='stop')throw new Error('offline');});
    const talk=createPushToTalk({listen},s=>statuses.push(s));
    talk.press(); await flush(); talk.release(); await talk.settled(); talk.press();
    expect(listen.mock.calls).toEqual([['start'],['stop'],['stop']]);
    expect(statuses.at(-1)?.message).toContain('Close Artemis');
  });
});
