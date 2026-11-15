import {test, expect, type Page} from '@playwright/test';
import {createServer, type ServerResponse} from 'node:http';

// startApproval overrides the approval sent at connect; decisions records what the core was told.
async function fixture(approvalAtStart = false, activeTask = false, startApproval?: object) {
  let events: ServerResponse | undefined;
  let seq = 0;
  const commands: string[] = [];
  const decisions: string[] = [];
  const emit = (type: string, data: object) => events!.write(`id: ${++seq}\ndata: ${JSON.stringify({seq, session_id:'ses_focus', task_id:'tsk_focus', at:new Date().toISOString(), type, data, announce:null})}\n\n`);
  const server = createServer(async (req, res) => {
    res.setHeader('access-control-allow-origin', 'http://127.0.0.1:1420');
    res.setHeader('access-control-allow-headers', 'Authorization,Content-Type,Last-Event-ID');
    res.setHeader('access-control-allow-methods', 'GET,POST,OPTIONS');
    if (req.method === 'OPTIONS') {res.writeHead(204).end(); return;}
    if (req.headers.authorization !== 'Bearer focus-test') {res.writeHead(401).end(); return;}
    if (req.url === '/sessions/ses_focus/events') {
      events = res;
      res.writeHead(200, {'content-type':'text/event-stream'});
      res.write(': connected\n\n');
      if (activeTask && seq === 0) emit('task.state', {state:'working'});
      if (approvalAtStart && seq === 0) emit('approval.requested', startApproval ?? {approval_id:'apr_focus', approval_hash:'sha256:focus', kind:'write_files', summary:'Focus fixture proposal', files:[], diff:null, run:null, findings:[]});
      return;
    }
    if (req.url === '/approvals/apr_focus') {
      let body = ''; req.on('data', c => body += c);
      await new Promise(r => req.on('end', r));
      decisions.push(JSON.parse(body).decision);
      emit('approval.resolved', {approval_id:'apr_focus', decision:'reject', by:'user'});
      res.writeHead(204).end(); return;
    }
    if (req.url === '/speech' || req.url?.endsWith('/cancel')) {commands.push(req.url); res.writeHead(204).end(); return;}
    res.setHeader('content-type', 'application/json');
    if (req.url === '/health') res.end(JSON.stringify({ok:true, version:'focus-fixture', voice_ready:false, screen_reader:null}));
    else if (req.url === '/sessions') res.end(JSON.stringify({session_id:'ses_focus'}));
    else res.writeHead(404).end();
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  return {
    commands, decisions,
    drop: () => events!.end(),
    async connect(page: Page) {
      await page.goto('/');
      await page.getByLabel('Core address').fill(`http://127.0.0.1:${(server.address() as {port:number}).port}`);
      await page.getByLabel('Launch token').fill('focus-test');
      await page.getByRole('button', {name:'Connect to core', exact:true}).click();
    },
    async reconnect(page: Page, whileOffline?: () => Promise<void>) {
      events!.end();
      await expect(page.locator('#request')).toHaveAttribute('aria-disabled', 'true');
      await whileOffline?.();
      await expect(page.getByRole('status')).toHaveText('Connected');
    },
    async close() {
      server.closeAllConnections();
      await new Promise<void>(resolve => server.close(() => resolve()));
    },
  };
}

test('reconnect preserves request, log, control and document focus', async ({page}) => {
  const core = await fixture();
  try {
    await core.connect(page);
    const request = page.getByLabel('What would you like to build?');
    await expect(request).toBeFocused();
    await request.fill('draft request');
    await core.reconnect(page, async () => {
      await expect(request).toBeFocused();
      await expect(request).toHaveJSProperty('readOnly', true);
      await page.keyboard.type('offline edit');
      await expect(request).toHaveValue('draft request');
      await expect(page.getByRole('button', {name:'Send request', exact:true})).toBeDisabled();
    });
    await expect(request).toBeFocused();
    await expect(request).toHaveJSProperty('readOnly', false);
    const log = page.getByRole('log');
    await log.focus();
    await core.reconnect(page);
    await expect(log).toBeFocused();
    const disconnect = page.getByRole('button', {name:'Disconnect and reset', exact:true});
    await disconnect.focus();
    await core.reconnect(page);
    await expect(disconnect).toBeFocused();
    // Reading non-focusable content may leave system focus on body.
    await log.focus();
    await log.evaluate(el => (el as HTMLElement).blur());
    await core.reconnect(page);
    await expect.poll(() => page.evaluate(() => document.activeElement === document.body)).toBe(true);
  } finally {await core.close();}
});

test('initial approval and later log focus retain ownership across reconnect', async ({page}) => {
  const core = await fixture(true);
  try {
    await core.connect(page);
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect.poll(() => dialog.evaluate(el => el.contains(document.activeElement))).toBe(true);
    await core.reconnect(page);
    await expect(dialog).toBeVisible();
    await expect.poll(() => dialog.evaluate(el => el.contains(document.activeElement))).toBe(true);
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(page.locator('#request')).toBeFocused();
    const log = page.getByRole('log');
    await log.focus();
    await core.reconnect(page);
    await expect(log).toBeFocused();
  } finally {await core.close();}
});


test('offline task and speech controls retain focus and ignore Enter; manual reconnect returns to request', async ({page}) => {
  const core = await fixture(false, true);
  try {
    await core.connect(page);
    await expect(page.getByText('Task: working', {exact:true})).toBeVisible();
    for (const name of ['Cancel task', 'Repeat last speech', 'Stop speech']) {
      const control = page.getByRole('button', {name, exact:true});
      await control.focus();
      await core.reconnect(page, async () => {
        await expect(control).toBeFocused();
        await expect(control).toHaveAttribute('aria-disabled', 'true');
        await page.keyboard.press('Enter');
        expect(core.commands).toEqual([]);
      });
      await expect(control).toBeFocused();
    }
    core.drop();
    const reconnect = page.getByRole('button', {name:'Reconnect event stream', exact:true});
    await reconnect.click();
    await expect(page.getByRole('status')).toHaveText('Connected');
    await expect(page.locator('#request')).toBeFocused();
  } finally {await core.close();}
});

const desk = {approval_id:'apr_focus', approval_hash:'sha256:focus', summary:'Desktop step', files:[], diff:null, run:null, findings:[]};

test('desktop approval shows the exact command word for word', async ({page}) => {
  const core = await fixture(true, false, {...desk, kind:'run_command', desktop:{action:'run_command', target:'~/proj', text:'echo "a  b" && ls'}});
  try {
    await core.connect(page);
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('echo "a  b" && ls');
    await expect(dialog).toContainText('~/proj');
    await expect(dialog.getByRole('button', {name:'Approve', exact:true})).toBeVisible();
  } finally {await core.close();}
});

test('approval the client cannot show offers only Reject, and Enter rejects it', async ({page}) => {
  const core = await fixture(true, false, {...desk, kind:'run_command'});
  try {
    await core.connect(page);
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText("Can't show this approval, rejecting it");
    await expect(dialog.getByRole('button')).toHaveCount(1);
    await expect(dialog.getByRole('button', {name:'Reject', exact:true})).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(dialog).toHaveCount(0);
    expect(core.decisions).toEqual(['reject']);
  } finally {await core.close();}
});
