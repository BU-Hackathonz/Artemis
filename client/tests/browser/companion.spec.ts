import {test, expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('compact status has keyboard access to controls and never opens a core session', async ({page}) => {
  await page.addInitScript(() => {
    const probe = {status:{connection:'ready', task:'working', microphone:'idle', approval:false, speech_ready:true}, commands:[] as string[]};
    Object.defineProperty(window, 'companionProbe', {value:probe});
    Object.defineProperty(window, '__TAURI_INTERNALS__', {value:{invoke:async (command:string) => {
      probe.commands.push(command);
      if (command === 'get_companion_status') return {...probe.status};
      if (command === 'show_main') return;
      throw new Error('Unexpected command');
    }}});
  });
  const coreRequests:string[] = [];
  page.on('request', request => {if (/\/(?:health|sessions)(?:\/|$)/.test(new URL(request.url()).pathname)) coreRequests.push(request.url());});
  await page.setViewportSize({width:420, height:104});
  await page.goto('/#companion');
  await expect(page.getByText('Task working', {exact:true})).toBeVisible();
  const open = page.getByRole('button', {name:'Open controls', exact:true});
  await expect(open).toBeFocused();
  await expect(open).toHaveAccessibleDescription('Task working');
  await expect(page.locator('#desktop-status')).toHaveAttribute('aria-live','off');
  expect((await new AxeBuilder({page}).analyze()).violations).toEqual([]);
  await page.keyboard.press('Enter');
  await expect.poll(() => page.evaluate(() => (window as unknown as {companionProbe:{commands:string[]}}).companionProbe.commands.filter(c => c === 'show_main').length)).toBe(1);
  await page.evaluate(() => {(window as unknown as {companionProbe:{status:{approval:boolean}}}).companionProbe.status.approval = true;});
  await expect(page.getByText('Approval needed · open controls', {exact:true})).toBeVisible();
  await expect(open).toHaveAccessibleDescription('Approval needed · open controls');
  await page.keyboard.press('Escape');
  await expect.poll(() => page.evaluate(() => (window as unknown as {companionProbe:{commands:string[]}}).companionProbe.commands.filter(c => c === 'show_main').length)).toBe(2);
  expect(coreRequests).toEqual([]);
  expect(await page.evaluate(() => (window as unknown as {companionProbe:{commands:string[]}}).companionProbe.commands.every(c => ['get_companion_status','show_main'].includes(c)))).toBe(true);
});

for (const background of [true, false]) {
  test(`native approval focuses ${background ? 'Reject when revealing background controls' : 'Approve in existing foreground controls'}`, async ({page}) => {
    const {createServer} = await import('node:http');
    let events: import('node:http').ServerResponse | undefined;
    const decisions:string[] = [];
    const emit = (seq:number, type:string, data:object) => events!.write(`id: ${seq}\ndata: ${JSON.stringify({seq, session_id:'ses_approval', task_id:'tsk_approval', at:new Date().toISOString(), type, data, announce:null})}\n\n`);
    const server = createServer((req,res) => {
      res.setHeader('access-control-allow-origin','http://127.0.0.1:1420');
      res.setHeader('access-control-allow-headers','Authorization,Content-Type,Last-Event-ID');
      res.setHeader('access-control-allow-methods','GET,POST,OPTIONS');
      if (req.method === 'OPTIONS') {res.writeHead(204).end(); return;}
      if (req.headers.authorization !== 'Bearer approval-focus-test') {res.writeHead(401).end(); return;}
      if (req.url === '/sessions/ses_approval/events') {
        events=res;res.writeHead(200,{'content-type':'text/event-stream'});res.write(': connected\n\n');
        emit(1,'approval.requested',{approval_id:'apr_notch', approval_hash:'sha256:notch', kind:'write_files', summary:'Synthetic focus proposal', files:[], diff:null, run:null, findings:[]}); return;
      }
      if (req.url === '/approvals/apr_notch') {
        let body='';req.on('data',part=>{body+=part;});req.on('end',()=>{
          decisions.push(JSON.parse(body).decision);
          emit(2,'approval.resolved',{approval_id:'apr_notch',decision:decisions.at(-1),by:'user'});
          res.writeHead(204).end();
        }); return;
      }
      if (req.url === '/sessions/ses_approval/ready') {res.writeHead(204).end();return;}
      res.setHeader('content-type','application/json');
      if (req.url === '/health') res.end(JSON.stringify({ok:true,version:'approval-focus-fixture',voice_ready:true,screen_reader:null}));
      else if (req.url === '/sessions') res.end(JSON.stringify({session_id:'ses_approval'}));
      else res.writeHead(404).end();
    });
    await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
    try {
      await page.addInitScript(({url,background}) => {
        const reveals:boolean[]=[]; Object.defineProperty(window,'approvalReveals',{value:reveals});
        const focused:string[]=[]; Object.defineProperty(window,'approvalFocusHistory',{value:focused});
        if(background) Object.defineProperty(document,'hasFocus',{value:()=>false});
        document.addEventListener('focusin',event=>{
          const target=event.target;
          if(target instanceof HTMLButtonElement && target.closest('[role=dialog]')) focused.push(target.textContent??'');
        });
        Object.defineProperty(window,'__TAURI_INTERNALS__',{value:{transformCallback:()=>1,unregisterCallback:()=>{},invoke:async(command:string,args?:{status?:{approval:boolean}})=>{
          if(command==='reveal_approval'){reveals.push(Boolean(document.activeElement?.hasAttribute('data-approval-reject')));return;}
          if(command==='get_core_config')return{core_url:url,token:'approval-focus-test'};
          if(command==='update_companion_status')return background && Boolean(args?.status?.approval);
          if(command.endsWith('is_registered'))return false;
          return;
        }}});
      }, {url:`http://127.0.0.1:${(server.address() as {port:number}).port}`,background});
      await page.goto('/');
      const decision=background?'Reject':'Approve';
      await expect(page.getByRole('dialog')).toBeVisible();
      await expect(page.getByRole('button',{name:decision,exact:true})).toBeFocused();
      if(background) {
        await expect.poll(()=>page.evaluate(()=>(window as unknown as {approvalReveals:boolean[]}).approvalReveals)).toEqual([true]);
        const history=await page.evaluate(()=>(window as unknown as {approvalFocusHistory:string[]}).approvalFocusHistory);
        expect(history.length).toBeGreaterThan(0);
        expect(history.some(name=>name.startsWith('Approve'))).toBe(false);
      }
      else expect(await page.evaluate(()=>(window as unknown as {approvalReveals:boolean[]}).approvalReveals)).toEqual([]);
      await page.keyboard.press('Enter');
      await expect.poll(()=>decisions).toEqual([decision.toLowerCase()]);
    } finally {server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}
  });
}


test('recovered polling clears its feedback without erasing a failed Open controls action', async ({page}) => {
  await page.addInitScript(() => {
    const probe={failPoll:true,failOpen:false,polls:0};
    Object.defineProperty(window,'feedbackProbe',{value:probe});
    Object.defineProperty(window,'__TAURI_INTERNALS__',{value:{invoke:async(command:string)=>{
      if(command==='get_companion_status') {
        probe.polls++;
        if(probe.failPoll) throw new Error('Transient native failure');
        return{connection:'ready',task:'working',microphone:'idle',approval:false,speech_ready:true};
      }
      if(command==='show_main') {if(probe.failOpen) throw new Error('Native focus failure');return;}
      throw new Error('Unexpected command');
    }}});
  });
  await page.goto('/#companion');
  await expect(page.getByRole('status')).toHaveText('Open controls to check Artemis.');
  await page.evaluate(() => {(window as unknown as {feedbackProbe:{failPoll:boolean}}).feedbackProbe.failPoll=false;});
  const open=page.getByRole('button',{name:'Open controls',exact:true});
  await expect(open).toHaveAccessibleDescription('Task working');
  await expect(page.getByRole('status')).toHaveCount(0);
  await page.evaluate(() => {(window as unknown as {feedbackProbe:{failOpen:boolean}}).feedbackProbe.failOpen=true;});
  await open.click();
  await expect(page.getByRole('alert')).toContainText('Could not open controls.');
  const polls=await page.evaluate(()=>(window as unknown as {feedbackProbe:{polls:number}}).feedbackProbe.polls);
  await expect.poll(()=>page.evaluate(()=>(window as unknown as {feedbackProbe:{polls:number}}).feedbackProbe.polls)).toBeGreaterThan(polls);
  await expect(page.getByRole('alert')).toContainText('Could not open controls.');
});
