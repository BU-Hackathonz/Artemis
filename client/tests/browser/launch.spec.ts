import {test, expect} from '@playwright/test';
import {createServer} from 'node:http';

async function mockNative(page: import('@playwright/test').Page, config: {core_url:string;token:string} | null) {
  await page.addInitScript(value => {
    const calls: string[] = [];
    Object.defineProperty(window,'nativeCalls',{value:calls});
    Object.defineProperty(window,'__TAURI_INTERNALS__',{value:{invoke:async (command: string) => {
      calls.push(command);
      if (!value) throw new Error('untrusted error containing launch-test-token');
      return value;
    }}});
  }, config);
}

test('native auto-connect uses one config command and authenticated HTTP/SSE with no form', async ({page}) => {
  const paths: string[] = [];
  const server = createServer((req,res) => {
    res.setHeader('access-control-allow-origin','http://127.0.0.1:1420');
    res.setHeader('access-control-allow-headers','Authorization,Content-Type,Last-Event-ID');
    res.setHeader('access-control-allow-methods','GET,POST,OPTIONS');
    if (req.method === 'OPTIONS') {res.writeHead(204).end(); return;}
    paths.push(req.url ?? '');
    if (req.headers.authorization !== 'Bearer launch-test-token') {res.writeHead(401).end(); return;}
    if (req.url === '/sessions/ses_native/events') {
      res.writeHead(200,{'content-type':'text/event-stream'});
      res.write(`id: 1\ndata: ${JSON.stringify({seq:1,session_id:'ses_native',task_id:null,at:new Date().toISOString(),type:'agent.message',data:{text:'Core connected'},announce:{text:'Core connected',priority:'normal'}})}\n\n`);
      return;
    }
    res.setHeader('content-type','application/json');
    if (req.url === '/health') res.end(JSON.stringify({ok:true,version:'native-fixture',voice_ready:false,screen_reader:null}));
    else if (req.url === '/sessions') res.end(JSON.stringify({session_id:'ses_native'}));
    else res.writeHead(404).end();
  });
  await new Promise<void>(resolve => server.listen(0,'127.0.0.1',resolve));
  const address = server.address() as {port:number};
  try {
    await mockNative(page,{core_url:`http://127.0.0.1:${address.port}`,token:'launch-test-token'});
    await page.goto('/');
    await expect(page.getByRole('status')).toHaveText('Connected');
    await expect(page.getByRole('log')).toContainText('Core connected');
    await expect(page.getByLabel('Core address')).toHaveCount(0);
    await expect(page.getByLabel('Launch token')).toHaveCount(0);
    await expect(page.getByRole('button',{name:'Try in-memory demo',exact:true})).toHaveCount(0);
    await expect(page.getByLabel('What would you like to build?')).toBeFocused();
    expect(await page.evaluate(() => (window as unknown as {nativeCalls:string[]}).nativeCalls)).toEqual(['get_core_config']);
    expect(paths).toEqual(['/health','/sessions','/sessions/ses_native/events']);
    expect(await page.locator('body').innerText()).not.toContain('launch-test-token');
    expect(page.url()).not.toContain('launch-test-token');
    expect(await page.evaluate(() => [localStorage.length,sessionStorage.length])).toEqual([0,0]);
  } finally {
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});

for (const invalid of [null,{core_url:'https://example.com',token:'launch-test-token'}]) {
  test(`native ${invalid ? 'remote' : 'missing'} configuration fails without manual/demo fallback`, async ({page}) => {
    const coreRequests: string[] = [];
    page.on('request', request => {if (request.url().includes('/health')) coreRequests.push(request.url());});
    await mockNative(page,invalid);
    await page.goto('/');
    await expect(page.getByRole('status')).toHaveText('Launch failed');
    await expect(page.getByRole('alert')).toContainText(invalid ? 'loopback' : 'infra/start.ps1');
    await expect(page.getByLabel('Core address')).toHaveCount(0);
    await expect(page.getByLabel('Launch token')).toHaveCount(0);
    await expect(page.getByRole('button',{name:'Try in-memory demo',exact:true})).toHaveCount(0);
    await expect(page.getByRole('button',{name:'Retry launcher connection',exact:true})).toBeEnabled();
    expect(await page.locator('body').innerText()).not.toContain('launch-test-token');
    expect(coreRequests).toEqual([]);
  });
}
