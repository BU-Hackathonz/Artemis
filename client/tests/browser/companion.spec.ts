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
  await page.setViewportSize({width:420, height:64});
  await page.goto('/#companion');
  await expect(page.getByText('Task working', {exact:true})).toBeVisible();
  const open = page.getByRole('button', {name:'Open controls', exact:true});
  await expect(open).toBeFocused();
  expect((await new AxeBuilder({page}).analyze()).violations).toEqual([]);
  await page.keyboard.press('Enter');
  await expect.poll(() => page.evaluate(() => (window as unknown as {companionProbe:{commands:string[]}}).companionProbe.commands.filter(c => c === 'show_main').length)).toBe(1);
  await page.evaluate(() => {(window as unknown as {companionProbe:{status:{approval:boolean}}}).companionProbe.status.approval = true;});
  await expect(page.getByText('Approval needed · open controls', {exact:true})).toBeVisible();
  await page.keyboard.press('Escape');
  await expect.poll(() => page.evaluate(() => (window as unknown as {companionProbe:{commands:string[]}}).companionProbe.commands.filter(c => c === 'show_main').length)).toBe(2);
  expect(coreRequests).toEqual([]);
  expect(await page.evaluate(() => (window as unknown as {companionProbe:{commands:string[]}}).companionProbe.commands.every(c => ['get_companion_status','show_main'].includes(c)))).toBe(true);
});

// Compact status is opt-in (start.ps1 -Compact). The default launch keeps Approve focus even in the background.
// Background reveals focus Approve too; Enter is ignored for 600 ms after the reveal, Escape is not guarded.
const cases: {compact:boolean; background:boolean; press:'guard'|'escape'|'enter'; title:string}[] = [
  {compact:true, background:true, press:'guard', title:'compact launch: background reveal focuses Approve, ignores Enter for 600 ms, then Enter approves'},
  {compact:true, background:true, press:'escape', title:'compact launch: Escape during the background reveal guard rejects'},
  {compact:true, background:false, press:'enter', title:'compact launch: foreground approval focuses Approve and Enter approves immediately'},
  {compact:false, background:true, press:'enter', title:'default launch: approval focuses Approve with no compact status calls'},
];
for (const {compact, background, press, title} of cases) {
  test(title, async ({page}) => {
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
      await page.addInitScript(({url,compact,background}) => {
        const reveals:boolean[]=[]; Object.defineProperty(window,'approvalReveals',{value:reveals});
        const commands:string[]=[]; Object.defineProperty(window,'nativeCommands',{value:commands});
        const focused:string[]=[]; Object.defineProperty(window,'approvalFocusHistory',{value:focused});
        if(background) Object.defineProperty(document,'hasFocus',{value:()=>false});
        document.addEventListener('focusin',event=>{
          const target=event.target;
          if(target instanceof HTMLButtonElement && target.closest('[role=dialog]')) focused.push(target.textContent??'');
        });
        Object.defineProperty(window,'__TAURI_INTERNALS__',{value:{transformCallback:()=>1,unregisterCallback:()=>{},invoke:async(command:string,args?:{status?:{approval:boolean}})=>{
          commands.push(command);
          if(command==='reveal_approval'){reveals.push(Boolean(document.activeElement?.hasAttribute('data-approval-approve')));return;}
          if(command==='get_core_config')return{core_url:url,token:'approval-focus-test',compact};
          if(command==='update_companion_status')return background && Boolean(args?.status?.approval);
          if(command.endsWith('is_registered'))return false;
          return;
        }}});
      }, {url:`http://127.0.0.1:${(server.address() as {port:number}).port}`,compact,background});
      await page.goto('/');
      await expect(page.getByRole('dialog')).toBeVisible();
      await expect(page.getByRole('button',{name:'Approve',exact:true})).toBeFocused();
      await expect(page.getByRole('button',{name:'Use compact status',exact:true,includeHidden:true})).toHaveCount(compact ? 1 : 0);
      if(!compact) {
        const commands=await page.evaluate(()=>(window as unknown as {nativeCommands:string[]}).nativeCommands);
        expect(commands.filter(c=>['update_companion_status','reveal_approval','enter_compact'].includes(c))).toEqual([]);
      }
      if(compact && background) {
        await expect.poll(()=>page.evaluate(()=>(window as unknown as {approvalReveals:boolean[]}).approvalReveals)).toEqual([true]);
        const history=await page.evaluate(()=>(window as unknown as {approvalFocusHistory:string[]}).approvalFocusHistory);
        expect(history.length).toBeGreaterThan(0);
        expect(history.some(name=>name.startsWith('Approve'))).toBe(true);
        expect(history.some(name=>name==='Reject')).toBe(false);
      }
      else expect(await page.evaluate(()=>(window as unknown as {approvalReveals:boolean[]}).approvalReveals)).toEqual([]);
      if(press==='guard') {
        await page.keyboard.press('Enter');
        await page.waitForTimeout(150);
        expect(decisions).toEqual([]);
        await expect(page.getByRole('dialog')).toBeVisible();
        await page.waitForTimeout(650);
        await page.keyboard.press('Enter');
        await expect.poll(()=>decisions).toEqual(['approve']);
      }
      else if(press==='escape') {
        await page.keyboard.press('Escape');
        await expect.poll(()=>decisions).toEqual(['reject']);
      }
      else {
        await page.keyboard.press('Enter');
        await expect.poll(()=>decisions).toEqual(['approve']);
      }
    } finally {server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}
  });
}
