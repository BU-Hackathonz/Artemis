import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('keyboard demo: separate write/run approvals, trapped focus, result and repeat request', async ({page}) => {
  await page.goto('/');
  await page.getByRole('button',{name:'Try in-memory demo'}).focus();
  await page.keyboard.press('Enter');
  const input = page.getByLabel('What would you like to build?');
  await input.fill('create a Python program that prints Hello World');
  await page.getByRole('button',{name:'Send request',exact:true}).focus();
  await page.keyboard.press('Enter');
  const write = page.getByRole('dialog',{name:'Review file changes'});
  await expect(write).toBeVisible();
  await expect(write.getByRole('button',{name:'Reject',exact:true})).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(write.getByRole('button',{name:'Approve',exact:true})).toBeFocused();
  for (let i=0; i<4; i++) {
    await page.keyboard.press('Tab');
    await expect.poll(() => write.evaluate(element => element.contains(document.activeElement))).toBe(true);
  }
  await write.getByRole('button',{name:'Approve',exact:true}).focus();
  expect((await new AxeBuilder({page}).analyze()).violations).toEqual([]);
  await page.keyboard.press('Enter');
  const run = page.getByRole('dialog',{name:'Review program run'});
  await expect(run).toBeVisible();
  await run.getByRole('button',{name:'Approve',exact:true}).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('log')).toContainText('Hello, World!');
  await expect(input).toBeFocused();
  expect((await new AxeBuilder({page}).analyze()).violations).toEqual([]);
  await input.fill('another request');
  await page.getByRole('button',{name:'Send request',exact:true}).click();
  await expect(page.getByRole('dialog')).toBeVisible();
});

test('Escape rejects without changing files and returns focus', async ({page}) => {
  await page.goto('/');
  await page.getByRole('button',{name:'Try in-memory demo'}).click();
  await page.getByLabel('What would you like to build?').fill('hello');
  await page.getByRole('button',{name:'Send request',exact:true}).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('log')).toContainText('proposal rejected; no action taken');
  await expect(page.getByRole('log')).not.toContainText('main.py created');
});

test('connection failure is actionable; credentials are never persisted', async ({page}) => {
  await page.goto('/');
  await page.getByLabel('Core address').fill('https://example.com');
  await page.getByLabel('Launch token').fill('test-only');
  await page.getByRole('button',{name:'Connect to core',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('loopback');
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0,0]);
});

test('authenticated SSE resumes and deduplicates replay', async ({page}) => {
  let streams = 0;
  const streamHeaders: Record<string,string>[] = [];
  const posted: unknown[] = [];
  const approval = (id: string) => ({approval_id:id, approval_hash:`sha256:${id}`, kind:'write_files', summary:`Proposal ${id}`, files:[], diff:null, run:null, findings:[]});
  function event(seq: number, type: string, data: object) {
    return `id: ${seq}\ndata: ${JSON.stringify({seq,session_id:'ses_1',task_id:'tsk_1',type,data,at:new Date().toISOString(),announce:{text:`Event ${seq}`,priority:'normal'}})}\n\n`;
  }
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const headers = {'access-control-allow-origin':'*','access-control-allow-headers':'Authorization,Content-Type,Last-Event-ID','access-control-allow-methods':'GET,POST,OPTIONS'};
    if (request.method() === 'OPTIONS') {await route.fulfill({status:204,headers}); return;}
    expect(request.headers().authorization).toBe('Bearer launch-test');
    if (path === '/health') await route.fulfill({json:{ok:true,version:'test',voice_ready:true,screen_reader:'nvda'},headers});
    else if (path === '/sessions') await route.fulfill({json:{session_id:'ses_1'},headers});
    else if (path.endsWith('/events')) {
      streams++; streamHeaders.push(request.headers());
      const body = streams === 1 ? event(1,'approval.requested',approval('apr_1')) : streams === 2 ? event(1,'approval.requested',approval('apr_1')) + event(2,'approval.resolved',{approval_id:'apr_1',decision:'reject',by:'invalidated'}) + event(3,'approval.requested',approval('apr_2')) : event(4,'voice.status',{voice_ready:false,screen_reader:null});
      await route.fulfill({headers:{...headers,'content-type':'text/event-stream'},body});
    } else if (path === '/approvals/apr_1') {
      posted.push(request.postDataJSON());
      await route.fulfill({status:409,headers});
    } else await route.fulfill({status:204,headers});
  });
  await page.goto('/');
  await page.getByLabel('Core address').fill('http://127.0.0.1:8765');
  await page.getByLabel('Launch token').fill('launch-test');
  await page.getByRole('button',{name:'Connect to core',exact:true}).click();
  // Stream closure temporarily disables decisions; replay restores a fresh proposal.
  await expect.poll(() => streams).toBeGreaterThan(1);
  await expect.poll(() => streamHeaders[1]?.['last-event-id']).toBe('1');
  await expect(page.getByRole('log')).toHaveAttribute('aria-live','off');
  await expect(page.getByRole('log').getByText('Event 1',{exact:true})).toHaveCount(1);
  await page.getByRole('button',{name:'Reconnect event stream',exact:true}).click();
  await expect.poll(() => streamHeaders[2]?.['last-event-id']).toBe('3');
  await expect.poll(() => streamHeaders[3]?.['last-event-id']).toBe('4');
  await expect(page.getByRole('log')).toHaveAttribute('aria-live','polite');
  expect(posted).toEqual([]);
});

test('active-task conflict, blocked override, stale hash and changing speech availability', async ({page}) => {
  const {createServer} = await import('node:http');
  let stream: import('node:http').ServerResponse | undefined;
  const decisions: {decision: string; approval_hash: string}[] = [];
  const server = createServer((req, res) => {
    res.setHeader('access-control-allow-origin','http://127.0.0.1:1420');
    res.setHeader('access-control-allow-headers','Authorization,Content-Type,Last-Event-ID');
    res.setHeader('access-control-allow-methods','GET,POST,OPTIONS');
    if (req.method === 'OPTIONS') {res.writeHead(204).end(); return;}
    if (req.headers.authorization !== 'Bearer launch-test') {res.writeHead(401).end(); return;}
    if (req.url === '/sessions/ses_1/events') {
      stream = res;
      res.writeHead(200,{'content-type':'text/event-stream'});
      res.write(': connected\n\n');
      return;
    }
    if (req.url?.startsWith('/approvals/')) {
      let body = '';
      req.on('data', chunk => {body += chunk;});
      req.on('end', () => {
        decisions.push(JSON.parse(body));
        res.writeHead(req.url === '/approvals/apr_old' ? 409 : 204).end();
      });
      return;
    }
    res.setHeader('content-type','application/json');
    if (req.url === '/health') res.end(JSON.stringify({ok:true,version:'test',voice_ready:true,screen_reader:'nvda'}));
    else if (req.url === '/sessions') res.end(JSON.stringify({session_id:'ses_1'}));
    else if (req.url === '/sessions/ses_1/tasks') {res.writeHead(409); res.end(JSON.stringify({error:'task_active',task_id:'tsk_active'}));}
    else res.writeHead(204).end();
  });
  await new Promise<void>(resolve => server.listen(0,'127.0.0.1',resolve));
  const address = server.address() as {port: number};
  let seq = 0;
  const emit = (type: string, data: object) => {
    seq++;
    stream!.write(`id: ${seq}\ndata: ${JSON.stringify({seq,session_id:'ses_1',task_id:'tsk_active',at:new Date().toISOString(),type,data,announce:null})}\n\n`);
  };
  const approval = (id: string) => ({approval_id:id,approval_hash:`sha256:${id}`,kind:'run_program',summary:'Blocked: high security finding',files:[],diff:null,run:{preset:'python',entry:'main.py',timeout_s:10},findings:[{severity:'high',rule:'B602',path:'main.py',line:1,message:'shell=True'}],blocking:true});
  try {
    await page.goto('/');
    await page.getByLabel('Core address').fill(`http://127.0.0.1:${address.port}`);
    await page.getByLabel('Launch token').fill('launch-test');
    await page.getByRole('button',{name:'Connect to core',exact:true}).click();
    await expect(page.getByRole('status')).toHaveText('Connected');
    const input = page.getByLabel('What would you like to build?');
    await input.fill('keep this request');
    await page.getByRole('button',{name:'Send request',exact:true}).click();
    await expect(page.getByRole('alert')).toContainText('Still working on the last request');
    await expect(input).toHaveValue('keep this request');
    await expect(page.getByRole('button',{name:'Cancel task',exact:true})).toBeEnabled();
    emit('task.state',{state:'awaiting_approval'});
    emit('approval.requested',approval('apr_old'));
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('button',{name:'Reject',exact:true})).toBeFocused();
    const approve = dialog.getByRole('button',{name:'Approve blocked action (Shift+Enter)',exact:true});
    await approve.focus();
    await page.keyboard.press('Enter');
    await approve.click();
    expect(decisions).toHaveLength(0);
    await approve.focus();
    await page.keyboard.press('Shift+Enter');
    await expect(dialog.getByRole('alert')).toContainText('stale');
    await expect(approve).toBeDisabled();
    await expect(dialog.getByRole('button',{name:'Disconnect and reset',exact:true})).toBeEnabled();
    emit('approval.resolved',{approval_id:'apr_old',decision:'invalidated',by:'invalidated'});
    emit('approval.requested',approval('apr_new'));
    await expect(dialog.getByRole('button',{name:'Reject',exact:true})).toBeFocused();
    await approve.focus();
    await page.keyboard.press('Shift+Enter');
    await expect.poll(() => decisions.length).toBe(2);
    expect(decisions).toEqual([{decision:'approve',approval_hash:'sha256:apr_old'},{decision:'approve',approval_hash:'sha256:apr_new'}]);
    emit('approval.resolved',{approval_id:'apr_new',decision:'approve',by:'user'});
    emit('task.state',{state:'succeeded'});
    await expect(dialog).toHaveCount(0);
    emit('voice.status',{voice_ready:false,screen_reader:null});
    await expect(page.getByRole('log')).toHaveAttribute('aria-live','polite');
    await expect(page.getByRole('log')).toContainText('Core speech is unavailable');
    emit('voice.status',{voice_ready:true,screen_reader:'nvda'});
    await expect(page.getByRole('log')).toHaveAttribute('aria-live','off');
  } finally {
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});


test('manual blocked demo exposes the native-hardware test without a backend', async ({page}) => {
  await page.goto('/');
  await page.getByRole('button',{name:'Try blocked-action demo',exact:true}).click();
  const input = page.getByLabel('What would you like to build?');
  await input.fill('test a blocked run');
  await page.getByRole('button',{name:'Send request',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'Approve',exact:true}).click();
  const dialog = page.getByRole('dialog',{name:'Review program run'});
  await expect(dialog).toContainText('Synthetic finding for keyboard testing only');
  await expect(dialog.getByRole('button',{name:'Reject',exact:true})).toBeFocused();
  const approve = dialog.getByRole('button',{name:'Approve blocked action (Shift+Enter)',exact:true});
  await approve.focus();
  await page.keyboard.press('Enter');
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Shift+Enter');
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('log')).toContainText('No files were written and no code was run.');
  await expect(input).toBeFocused();
});
