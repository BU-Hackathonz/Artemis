import { afterEach, describe, it, expect, vi } from 'vitest';
import { createHttpCore } from '../src/api';
import type { Approval } from '../src/contracts';
afterEach(() => vi.unstubAllGlobals());
describe('core HTTP boundary', () => {
  it('rejects remote, credential-bearing and non-root addresses', () => {
    for (const url of ['https://example.com','http://localhost:8000','http://127.0.0.1:8000/path','http://user:pass@127.0.0.1:8000','http://127.0.0.1:8000/?token=secret']) {
      expect(() => createHttpCore(url,'secret')).toThrow('loopback');
    }
  });
  it('sends the exact approval hash with Bearer auth and refuses stale approvals', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null,{status:409}));
    vi.stubGlobal('fetch',fetchMock);
    const core = createHttpCore('http://127.0.0.1:8000','secret');
    await expect(core.decide({approval_id:'apr_1',approval_hash:'sha256:exact'} as Approval,'approve')).rejects.toThrow('stale');
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('http://127.0.0.1:8000/approvals/apr_1');
    expect(options.headers.Authorization).toBe('Bearer secret');
    expect(JSON.parse(options.body)).toEqual({decision:'approve',approval_hash:'sha256:exact'});
    expect(options.redirect).toBe('error');
  });
  it('uses authenticated voice actions and keeps release alive during page teardown', async () => {
    const fetchMock=vi.fn().mockImplementation(async()=>new Response(null,{status:204}));
    vi.stubGlobal('fetch',fetchMock);
    const core=createHttpCore('http://127.0.0.1:8000','secret');
    await core.listen('start'); await core.listen('stop');
    expect(fetchMock.mock.calls.map(call=>JSON.parse(call[1].body))).toEqual([{action:'start'},{action:'stop'}]);
    expect(fetchMock.mock.calls.map(call=>call[1].keepalive)).toEqual([false,true]);
    expect(fetchMock.mock.calls.every(call=>call[1].headers.Authorization==='Bearer secret')).toBe(true);
  });
  it('reports only the bounded shortcut diagnostic to the session', async () => {
    const fetchMock=vi.fn().mockResolvedValue(new Response(null,{status:204}));
    vi.stubGlobal('fetch',fetchMock);
    const core=createHttpCore('http://127.0.0.1:8000','secret');
    await core.reportShortcutFailure('ses_1','Control+Alt+Space');
    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:8000/sessions/ses_1/diagnostics');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({code:'shortcut_registration_failed',shortcut:'Control+Alt+Space'});
  });
  it('preserves a client request id across a failed submission retry', async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(Response.json({task_id:'tsk_1'}));
    vi.stubGlobal('fetch',fetchMock);
    const core = createHttpCore('http://127.0.0.1:8000','secret');
    await expect(core.submit('ses_1','hello','cr_1')).rejects.toThrow('offline');
    expect(await core.submit('ses_1','hello','cr_1')).toBe('tsk_1');
    expect(fetchMock.mock.calls.map(call => JSON.parse(call[1].body).client_request_id)).toEqual(['cr_1','cr_1']);
  });
});
