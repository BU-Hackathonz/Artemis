import {test, expect, type Page, type CDPSession} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import {createServer, type ServerResponse} from 'node:http';

type Bridge = {send:(state:'Pressed'|'Released')=>void; commands:{command:string;shortcut?:string}[]};
async function bridge(page:Page, url:string, failFirst:boolean|'always'=false) {
  await page.addInitScript(({url,failFirst})=>{
    let handler:{onmessage:(event:unknown)=>void}|undefined;
    let current=''; let attempts=0;
    const commands:{command:string;shortcut?:string}[]=[];
    Object.defineProperty(window,'voiceBridge',{value:{commands,send:(state:string)=>handler?.onmessage({state,shortcut:current,id:1})}});
    Object.defineProperty(window,'__TAURI_INTERNALS__',{value:{transformCallback:()=>1,unregisterCallback:()=>{},invoke:async(command:string,args?:{shortcuts?:string[];shortcut?:string;handler?:{onmessage:(event:unknown)=>void}})=>{
      commands.push({command,shortcut:args?.shortcuts?.[0]??args?.shortcut});
      if(command==='get_core_config')return{core_url:url,token:'voice-test-token'};
      if(command==='plugin:global-shortcut|is_registered')return false;
      if(command==='plugin:global-shortcut|register'){
        if(failFirst==='always' || (failFirst===true && ++attempts===1))throw new Error('taken');
        current=args!.shortcuts![0];handler=args!.handler;return;
      }
      if(command==='plugin:global-shortcut|unregister'){handler=undefined;return;}
      throw new Error('unexpected native command');
    }}});
  },{url,failFirst});
}
async function coreFixture({failFirstStart=false,delayFirstStart=false,voiceReady=false,diagnosticsStatus=204,heard=[]}: {failFirstStart?:boolean;delayFirstStart?:boolean;voiceReady?:boolean;diagnosticsStatus?:number;heard?:string[]} = {}) {
  const actions:string[]=[];
  const transcripts=[...heard];
  const speech:string[]=[];
  const tasks:unknown[]=[];
  let pendingStart:ServerResponse|undefined;
  let events:ServerResponse|undefined;
  const diagnostics:unknown[]=[];
  let starts=0;
  let seq=0;
  const readies:unknown[]=[];
  const server=createServer((req,res)=>{
    res.setHeader('access-control-allow-origin','http://127.0.0.1:1420');
    res.setHeader('access-control-allow-headers','Authorization,Content-Type,Last-Event-ID');
    res.setHeader('access-control-allow-methods','GET,POST,OPTIONS');
    if(req.method==='OPTIONS'){res.writeHead(204).end();return;}
    if(req.headers.authorization!=='Bearer voice-test-token'){res.writeHead(401).end();return;}
    if(req.url==='/sessions/ses_voice/events'){
      events=res;res.writeHead(200,{'content-type':'text/event-stream'});res.write(': connected\n\n');return;
    }
    if(req.url==='/sessions/ses_voice/ready'){
      let body='';req.on('data',part=>{body+=part;});req.on('end',()=>{
        readies.push(JSON.parse(body));
        events?.write(`id: ${++seq}\ndata: ${JSON.stringify({seq,session_id:'ses_voice',task_id:null,at:new Date().toISOString(),type:'agent.message',data:{text:'Artemis is ready.'},announce:{text:'Artemis is ready.',priority:'normal'}})}\n\n`);
        res.writeHead(204).end();
      });return;
    }
    if(req.url==='/sessions/ses_voice/diagnostics'){
      let body='';req.on('data',part=>{body+=part;});req.on('end',()=>{
        diagnostics.push(JSON.parse(body));
        if(diagnosticsStatus===204)events?.write(`id: ${++seq}\ndata: ${JSON.stringify({seq,session_id:'ses_voice',task_id:null,at:new Date().toISOString(),type:'error',data:{code:'shortcut_registration_failed',message:'Shortcut could not be registered',recovery:'Use Start listening.'},announce:{text:"The push-to-talk shortcut couldn't be registered. Use Start listening.",priority:'high'}})}\n\n`);
        res.writeHead(diagnosticsStatus).end();
      });return;
    }
    if(req.url==='/voice/listen'){
      let body='';req.on('data',part=>{body+=part;});req.on('end',()=>{
        const action=JSON.parse(body).action;actions.push(action);
        if(action==='start' && ++starts===1){
          if(failFirstStart){res.writeHead(503).end();return;}
          if(delayFirstStart){pendingStart=res;return;}
        }
        res.writeHead(204).end();
        // Like the core: the transcript arrives as an event after stop (contracts.md, /voice/listen).
        const text=action==='stop'?transcripts.shift():undefined;
        if(text)events?.write(`id: ${++seq}\ndata: ${JSON.stringify({seq,session_id:'ses_voice',task_id:null,at:new Date().toISOString(),type:'transcript.final',data:{text,client_request_id:`cr_${seq}`},announce:{text:`Heard: ${text}`,priority:'normal'}})}\n\n`);
      });return;
    }
    if(req.url==='/speech' || req.url==='/sessions/ses_voice/tasks'){
      let body='';req.on('data',part=>{body+=part;});req.on('end',()=>{
        if(req.url==='/speech'){speech.push(JSON.parse(body).action);res.writeHead(204).end();return;}
        tasks.push(JSON.parse(body));res.writeHead(200,{'content-type':'application/json'}).end(JSON.stringify({task_id:'tsk_voice'}));
      });return;
    }
    res.setHeader('content-type','application/json');
    if(req.url==='/health')res.end(JSON.stringify({ok:true,version:'voice-fixture',voice_ready:voiceReady,screen_reader:null}));
    else if(req.url==='/sessions')res.end(JSON.stringify({session_id:'ses_voice'}));
    else res.writeHead(404).end();
  });
  await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
  const url=`http://127.0.0.1:${(server.address() as {port:number}).port}`;
  return{actions,speech,tasks,diagnostics,readies,url,emit:(type:string,data:object,taskId:string|null=null)=>{
    events?.write(`id: ${++seq}\ndata: ${JSON.stringify({seq,session_id:'ses_voice',task_id:taskId,at:new Date().toISOString(),type,data,announce:null})}\n\n`);
  },finishStart:()=>pendingStart?.writeHead(204).end(),close:async()=>{server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}};
}
async function send(page:Page,state:'Pressed'|'Released') {await page.evaluate(state=>(window as unknown as {voiceBridge:Bridge}).voiceBridge.send(state),state);}

test('native key repeats, release after blur, configurable chord and unload cleanup',async({page})=>{
  const core=await coreFixture();
  try{
    await bridge(page,core.url);await page.goto('/');
    await expect(page.getByRole('status')).toHaveText('Connected');
    await expect(page.getByText('Global shortcut is registered.',{exact:true})).toBeVisible();
    expect((await new AxeBuilder({page}).analyze()).violations).toEqual([]);
    await send(page,'Pressed');await send(page,'Pressed');
    await expect.poll(()=>core.actions).toEqual(['start']);
    await expect(page.getByLabel('Microphone status')).toContainText('Listening.');
    await page.evaluate(()=>window.dispatchEvent(new Event('blur')));
    await send(page,'Released');
    await expect.poll(()=>core.actions).toEqual(['start','stop']);
    await expect(page.getByLabel('Microphone status')).toContainText('Microphone stopped');
    await page.getByLabel('Global voice shortcut').fill('Control+Alt+Space');
    await page.getByRole('button',{name:'Apply shortcut',exact:true}).click();
    await expect.poll(()=>page.evaluate(()=>(window as unknown as {voiceBridge:Bridge}).voiceBridge.commands.filter(c=>c.command==='plugin:global-shortcut|register').map(c=>c.shortcut))).toEqual(['Control+Shift+Space','Control+Alt+Space']);
    await page.getByLabel('Global voice shortcut').fill('Shift+A');
    await page.getByRole('button',{name:'Apply shortcut',exact:true}).click();
    await expect(page.getByRole('alert')).toContainText('another modifier');
    await page.getByLabel('Global voice shortcut').fill('Control+Alt+Delete');
    await page.getByRole('button',{name:'Apply shortcut',exact:true}).click();
    await expect(page.getByRole('alert')).toContainText('system shortcut');
    expect(await page.evaluate(()=>(window as unknown as {voiceBridge:Bridge}).voiceBridge.commands.filter(c=>c.command==='plugin:global-shortcut|register').length)).toBe(2);
    await send(page,'Pressed');await expect.poll(()=>core.actions.length).toBe(3);
    await expect(page.getByLabel('Microphone status')).toContainText('Listening.');
    await page.evaluate(()=>window.dispatchEvent(new Event('beforeunload')));
    await expect.poll(()=>core.actions).toEqual(['start','stop','start','stop']);
  }finally{await core.close();}
});

test('registration failure retains a focusable toggle that works with screen-reader clicks',async({page})=>{
  const core=await coreFixture();
  try{
    await bridge(page,core.url,true);await page.goto('/');
    await expect(page.getByRole('alert')).toContainText('Could not register');
    expect(core.diagnostics).toEqual([]);
    expect((await new AxeBuilder({page}).analyze()).violations).toEqual([]);
    const talk=page.getByRole('button',{name:'Start listening',exact:true});
    await expect(talk).toBeEnabled();await talk.focus();
    await talk.dispatchEvent('click',{detail:0});
    await expect.poll(()=>core.actions).toEqual(['start']);
    const stop=page.getByRole('button',{name:'Stop listening',exact:true});
    await expect(stop).toBeVisible();
    await expect(stop).toBeFocused();
    await stop.dispatchEvent('click',{detail:0});
    await expect.poll(()=>core.actions).toEqual(['start','stop']);
    await expect(page.getByLabel('Microphone status')).toContainText('Microphone stopped');
    await expect(page.getByRole('button',{name:'Start listening',exact:true})).toBeFocused();
    await page.getByLabel('Global voice shortcut').fill('Control+Alt+Space');
    await page.getByRole('button',{name:'Apply shortcut',exact:true}).click();
    await expect(page.getByText('Global shortcut is registered.',{exact:true})).toBeVisible();
    await expect(page.getByRole('alert')).toHaveCount(0);
  }finally{await core.close();}
});

test('release is sent while start is pending and rapid re-press waits for a stop barrier',async({page})=>{
  const core=await coreFixture({delayFirstStart:true});
  try{
    await bridge(page,core.url);await page.goto('/');
    await expect(page.getByText('Global shortcut is registered.',{exact:true})).toBeVisible();
    await send(page,'Pressed');await expect.poll(()=>core.actions).toEqual(['start']);
    await send(page,'Released');await send(page,'Pressed');
    await expect.poll(()=>core.actions).toEqual(['start','stop']);
    core.finishStart();
    await expect.poll(()=>core.actions).toEqual(['start','stop','stop','start']);
    await expect(page.getByLabel('Microphone status')).toContainText('Listening.');
    await send(page,'Released');await expect.poll(()=>core.actions.at(-1)).toBe('stop');
  }finally{await core.close();}
});


test('SAPI-ready registration failure uses the bounded core diagnostic without a duplicate local alert',async({page})=>{
  const core=await coreFixture({voiceReady:true});
  try{
    await bridge(page,core.url,'always');await page.goto('/');
    await expect.poll(()=>core.diagnostics.length).toBe(1);
    expect(core.readies).toEqual([{}]);
    expect(core.diagnostics).toEqual([{code:'shortcut_registration_failed',shortcut:'Control+Shift+Space'}]);
    await expect(page.locator('#shortcut-error')).toContainText('Could not register');
    await expect(page.getByRole('alert')).toHaveCount(0);
    await expect(page.getByRole('log')).toContainText("shortcut couldn't be registered");
    await expect(page.getByRole('log')).toHaveAttribute('aria-live','off');
    await expect(page.getByRole('button',{name:'Start listening',exact:true})).toBeEnabled();
    await page.getByLabel('Global voice shortcut').fill('Control+Alt+Space');
    await page.getByRole('button',{name:'Apply shortcut',exact:true}).click();
    await expect(page.locator('#shortcut-error')).toContainText('Could not register Control+Alt+Space');
    await expect.poll(()=>core.diagnostics.length).toBe(2);
    await expect(page.getByRole('alert')).toHaveCount(0);
    await page.getByRole('button',{name:'Apply shortcut',exact:true}).click();
    await expect(page.getByRole('alert')).toContainText('Could not register Control+Alt+Space');
    expect(core.diagnostics).toHaveLength(2);
  }finally{await core.close();}
});

test('a failed core diagnostic retains the local alert and listening fallback',async({page})=>{
  const core=await coreFixture({voiceReady:true,diagnosticsStatus:404});
  try{
    await bridge(page,core.url,true);await page.goto('/');
    await expect(page.getByRole('alert')).toContainText('Could not register');
    expect(core.diagnostics).toHaveLength(1);
    await expect(page.getByRole('button',{name:'Start listening',exact:true})).toBeEnabled();
  }finally{await core.close();}
});


test('listening toggle retries after microphone start fails',async({page})=>{
  const core=await coreFixture({failFirstStart:true});
  try{
    await bridge(page,core.url);await page.goto('/');
    const start=page.getByRole('button',{name:'Start listening',exact:true});
    await start.click();
    await expect(page.getByLabel('Microphone status')).toContainText('Microphone could not start');
    await expect(start).not.toHaveAttribute('aria-pressed');
    await start.click();
    await expect.poll(()=>core.actions).toEqual(['start','stop','start']);
    await expect(page.getByLabel('Microphone status')).toContainText('Listening.');
    await page.getByRole('button',{name:'Stop listening',exact:true}).click();
    await expect.poll(()=>core.actions).toEqual(['start','stop','start','stop']);
  }finally{await core.close();}
});

test('Tab from the request field reaches the listening toggle before wrapping to the top',async({page})=>{
  const core=await coreFixture();
  try{
    await bridge(page,core.url);await page.goto('/');
    await expect(page.getByRole('status')).toHaveText('Connected');
    const request=page.getByLabel('What would you like to build?');
    await expect(request).toBeFocused();
    const talk=page.getByRole('button',{name:'Start listening',exact:true});
    for(let i=0;i<10 && !(await talk.evaluate(el=>el===document.activeElement));i++){
      await page.keyboard.press('Tab');
      await expect(page.getByRole('link',{name:'Skip to request'})).not.toBeFocused();
    }
    await expect(talk).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(talk).not.toBeFocused();
  }finally{await core.close();}
});

// Chromium's computed role and name for the focused element: what a screen reader is told on focus.
async function focused(cdp:CDPSession):Promise<string> {
  const {result}=await cdp.send('Runtime.evaluate',{expression:'document.activeElement===document.body?null:document.activeElement'});
  if(!result.objectId)return 'page';
  const {nodes}=await cdp.send('Accessibility.getPartialAXTree',{objectId:result.objectId,fetchRelatives:false});
  await cdp.send('Runtime.releaseObject',{objectId:result.objectId});
  return `${nodes[0]?.role?.value} "${nodes[0]?.name?.value ?? ''}"`;
}
async function walk(page:Page,cdp:CDPSession,key:'Tab'|'Shift+Tab',until:string,max=15) {
  const seen=[await focused(cdp)];
  for(let i=0;i<max && seen.at(-1)!==until;i++){await page.keyboard.press(key);seen.push(await focused(cdp));}
  return seen;
}
const REQUEST='textbox "What would you like to build?"';
const TO_TOGGLE=[REQUEST,'button "Repeat last speech"','button "Stop speech"','log "Activity log"','button "Start listening"'];

test('demo.md row 4: Tab from the request field passes Repeat, Stop and the log to Start listening, with no trap',async({page})=>{
  const core=await coreFixture();
  try{
    await bridge(page,core.url);await page.goto('/');
    await expect(page.getByText('Global shortcut is registered.',{exact:true})).toBeVisible();
    const request=page.getByLabel('What would you like to build?');
    await expect(request).toBeFocused();
    await expect(request).toHaveValue('');
    // Idle with an empty field: nothing to send and nothing to cancel, so Tab skips both.
    await expect(page.getByRole('button',{name:'Send request',exact:true})).toBeDisabled();
    await expect(page.getByRole('button',{name:'Cancel task',exact:true})).toBeDisabled();
    const cdp=await page.context().newCDPSession(page);
    expect(await walk(page,cdp,'Tab','button "Start listening"')).toEqual(TO_TOGGLE);
    // No trap: onward Tab leaves the page after the voice controls and comes back round to the field.
    const onward=await walk(page,cdp,'Tab',REQUEST);
    expect(onward.slice(0,5)).toEqual(['button "Start listening"','textbox "Global voice shortcut"','button "Apply shortcut"','page','link "Skip to request"']);
    expect(onward.at(-1)).toBe(REQUEST);
    // With text in the field (typed, or a transcript after a voice request) Send request is a stop too.
    await request.fill('draft');
    const withText=[REQUEST,'button "Send request"',...TO_TOGGLE.slice(1)];
    expect(await walk(page,cdp,'Tab','button "Start listening"')).toEqual(withText);
    // Back the same way. Escape outside a dialog does nothing: focus and text stay, nothing is sent.
    for(const stop of [...withText].reverse()){
      expect(await focused(cdp)).toBe(stop);
      await page.keyboard.press('Escape');
      expect(await focused(cdp)).toBe(stop);
      if(stop!==REQUEST)await page.keyboard.press('Shift+Tab');
    }
    await expect(request).toHaveValue('draft');
    expect([core.actions,core.speech,core.tasks]).toEqual([[],[],[]]);
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.getByRole('alert')).toHaveCount(0);
  }finally{await core.close();}
});

test('demo.md keys, idle: Enter sends only a non-empty request; Enter on Repeat and Stop reaches the core',async({page})=>{
  const core=await coreFixture();
  try{
    await bridge(page,core.url);await page.goto('/');
    await expect(page.getByText('Global shortcut is registered.',{exact:true})).toBeVisible();
    const request=page.getByLabel('What would you like to build?');
    await expect(request).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(request).toBeFocused();
    await expect(request).toHaveValue('');
    for(const [name,action] of [['Repeat last speech','repeat'],['Stop speech','stop']]){
      await page.keyboard.press('Tab');
      const button=page.getByRole('button',{name,exact:true});
      await expect(button).toBeFocused();
      await page.keyboard.press('Enter');
      await expect.poll(()=>core.speech.at(-1)).toBe(action);
      await expect(button).toBeFocused();
    }
    expect(core.speech).toEqual(['repeat','stop']);
    await request.focus();
    await page.keyboard.type('hello');
    await page.keyboard.press('Enter');
    await expect.poll(()=>core.tasks).toEqual([{text:'hello',source:'keyboard',client_request_id:expect.any(String)}]);
    await expect(request).toHaveValue('');
    expect(core.actions).toEqual([]);
  }finally{await core.close();}
});

for(const voiceReady of [false,true]){
  test(`demo.md row 5: the Start listening toggle matches hold-to-talk (core speech ${voiceReady?'available':'unavailable'})`,async({page})=>{
    const core=await coreFixture({voiceReady,heard:['held','toggled with Enter','toggled with Space']});
    try{
      await bridge(page,core.url);await page.goto('/');
      await expect(page.getByText('Global shortcut is registered.',{exact:true})).toBeVisible();
      const request=page.getByLabel('What would you like to build?');
      const status=page.getByLabel('Microphone status');
      // NVDA reads the status line itself only when the core can't speak.
      await expect(status).toHaveAttribute('aria-live',voiceReady?'off':'polite');
      await status.evaluate(el=>{
        const seen:string[]=[];Object.defineProperty(window,'statusSeen',{value:seen});
        new MutationObserver(()=>{if(seen.at(-1)!==el.textContent)seen.push(el.textContent??'');}).observe(el,{childList:true,characterData:true,subtree:true});
      });
      const cycle=['Opening microphone. Wait for the listening tone, then speak.','Listening. Release Control+Shift+Space or choose Stop listening.','Stopping microphone. Waiting for the transcript.','Microphone stopped. Hold Control+Shift+Space or choose Start listening to speak again.'];
      // The status lines shown since the last check: the cycle in order. "Opening" and "Stopping" can pass
      // too quickly to render (React batches them with the next line); "Listening" and "stopped" must show.
      const expectCycle=async()=>{
        const seen=await page.evaluate(()=>(window as unknown as {statusSeen:string[]}).statusSeen.splice(0));
        expect(cycle.filter(line=>seen.includes(line))).toEqual(seen);
        expect(seen).toEqual(expect.arrayContaining([cycle[1],cycle[3]]));
      };
      const log=page.getByRole('log');

      // Row 2, hold-to-talk: one start, one stop, then "Heard: ..." in the log. The core handles the transcript,
      // so it stays out of the keyboard field (main's bba66c5).
      await send(page,'Pressed');
      await expect(status).toHaveText(cycle[1]);
      await send(page,'Released');
      await expect(status).toHaveText(cycle[3]);
      await expect(log).toContainText('Heard: held');
      await expect(request).toHaveValue('');
      expect(core.actions).toEqual(['start','stop']);
      await expectCycle();

      // Row 5, the toggle, from the keyboard (Enter, then Space): the same requests, status and result.
      for(const [key,heard] of [['Enter','toggled with Enter'],['Space','toggled with Space']]){
        const before=core.actions.length;
        const start=page.getByRole('button',{name:'Start listening',exact:true});
        await start.focus();
        await page.keyboard.press(key);
        const stop=page.getByRole('button',{name:'Stop listening',exact:true});
        await expect(stop).toBeFocused();
        await expect(status).toHaveText(cycle[1]);
        await expect(status).toHaveAttribute('aria-live',voiceReady?'off':'polite');
        // A label toggle: the name says what pressing does next, so no aria-pressed on top of it.
        await expect(stop).not.toHaveAttribute('aria-pressed');
        // Speaking: unlike the held chord, nothing is held, and the microphone stays on until the second press.
        await page.waitForTimeout(1500);
        await expect(status).toHaveText(cycle[1]);
        expect(core.actions.slice(before)).toEqual(['start']);
        await page.keyboard.press(key);
        await expect(start).toBeFocused();
        await expect(status).toHaveText(cycle[3]);
        await expect(start).not.toHaveAttribute('aria-pressed');
        await expect(log).toContainText(`Heard: ${heard}`);
        await expect(request).toHaveValue('');
        expect(core.actions.slice(before)).toEqual(['start','stop']);
        await expectCycle();
      }
      await expect(page.getByRole('alert')).toHaveCount(0);
    }finally{await core.close();}
  });
}


test('voice task approval closure cannot leave a transcript to resend as a typed request', async ({page}) => {
  const core=await coreFixture();
  const submissions:string[]=[];
  page.on('request',request=>{
    if(request.method()==='POST' && new URL(request.url()).pathname==='/sessions/ses_voice/tasks')submissions.push(request.postData()??'');
  });
  try {
    await bridge(page,core.url);await page.goto('/');
    await expect(page.getByRole('status')).toHaveText('Connected');
    const request=page.getByLabel('What would you like to build?');
    const transcript='Create a Python program that prints Hello World';
    core.emit('transcript.final',{text:transcript,client_request_id:'voice-original'});
    core.emit('task.state',{state:'queued'},'tsk_voice');
    core.emit('approval.requested',{approval_id:'apr_voice',approval_hash:'sha256:voice',kind:'write_files',summary:'Voice proposal',files:[],diff:null,run:null,findings:[]},'tsk_voice');
    await expect(page.getByRole('dialog')).toBeVisible();
    core.emit('approval.resolved',{approval_id:'apr_voice',decision:'reject',by:'user'},'tsk_voice');
    core.emit('task.state',{state:'canceled'},'tsk_voice');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(request).toBeFocused();
    await expect(request).toHaveValue('');
    await page.keyboard.press('Enter');
    await expect(page.getByRole('button',{name:'Send request',exact:true})).toBeDisabled();
    expect(submissions).toEqual([]);
    await expect(page.getByRole('log')).toContainText(transcript);
    await request.fill('My unfinished keyboard draft');
    core.emit('transcript.final',{text:'repeat',client_request_id:'voice-control',intent:'repeat'});
    await expect(page.getByRole('log')).toContainText('repeat');
    await expect(request).toHaveValue('My unfinished keyboard draft');
  } finally {await core.close();}
});
