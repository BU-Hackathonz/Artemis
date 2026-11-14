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
  await page.keyboard.press('Tab');
  await expect(write.locator('summary')).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(write.getByRole('button',{name:'Approve',exact:true})).toBeFocused();
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
      const body = streams === 1 ? event(1,'approval.requested',approval('apr_1')) : event(1,'approval.requested',approval('apr_1')) + event(2,'approval.resolved',{approval_id:'apr_1',decision:'reject',by:'invalidated'}) + event(3,'approval.requested',approval('apr_2'));
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
  expect(posted).toEqual([]);
});
