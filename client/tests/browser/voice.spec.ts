import {test, expect, type Page} from '@playwright/test';
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
async function coreFixture({delayFirstStart=false,voiceReady=false,diagnosticsStatus=204}: {delayFirstStart?:boolean;voiceReady?:boolean;diagnosticsStatus?:number} = {}) {
  const actions:string[]=[];
  let pendingStart:ServerResponse|undefined;
  let events:ServerResponse|undefined;
  const diagnostics:unknown[]=[];
  let starts=0;
  const server=createServer((req,res)=>{
    res.setHeader('access-control-allow-origin','http://127.0.0.1:1420');
    res.setHeader('access-control-allow-headers','Authorization,Content-Type,Last-Event-ID');
    res.setHeader('access-control-allow-methods','GET,POST,OPTIONS');
    if(req.method==='OPTIONS'){res.writeHead(204).end();return;}
    if(req.headers.authorization!=='Bearer voice-test-token'){res.writeHead(401).end();return;}
    if(req.url==='/sessions/ses_voice/events'){
      events=res;res.writeHead(200,{'content-type':'text/event-stream'});res.write(': connected\n\n');return;
    }
    if(req.url==='/sessions/ses_voice/diagnostics'){
      let body='';req.on('data',part=>{body+=part;});req.on('end',()=>{
        diagnostics.push(JSON.parse(body));
        if(diagnosticsStatus===204)events?.write(`id: 1\ndata: ${JSON.stringify({seq:1,session_id:'ses_voice',task_id:null,at:new Date().toISOString(),type:'error',data:{code:'shortcut_registration_failed',message:'Shortcut could not be registered',recovery:'Use Start listening.'},announce:{text:"The push-to-talk shortcut couldn't be registered. Use Start listening.",priority:'high'}})}\n\n`);
        res.writeHead(diagnosticsStatus).end();
      });return;
    }
    if(req.url==='/voice/listen'){
      let body='';req.on('data',part=>{body+=part;});req.on('end',()=>{
        const action=JSON.parse(body).action;actions.push(action);
        if(action==='start' && ++starts===1 && delayFirstStart){pendingStart=res;return;}
        res.writeHead(204).end();
      });return;
    }
    res.setHeader('content-type','application/json');
    if(req.url==='/health')res.end(JSON.stringify({ok:true,version:'voice-fixture',voice_ready:voiceReady,screen_reader:null}));
    else if(req.url==='/sessions')res.end(JSON.stringify({session_id:'ses_voice'}));
    else res.writeHead(404).end();
  });
  await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
  const url=`http://127.0.0.1:${(server.address() as {port:number}).port}`;
  return{actions,diagnostics,url,finishStart:()=>pendingStart?.writeHead(204).end(),close:async()=>{server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}};
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
    await page.getByLabel('Global voice shortcut').fill('Control+Shift+Space');
    await page.getByRole('button',{name:'Apply shortcut',exact:true}).click();
    await expect.poll(()=>page.evaluate(()=>(window as unknown as {voiceBridge:Bridge}).voiceBridge.commands.filter(c=>c.command==='plugin:global-shortcut|register').map(c=>c.shortcut))).toEqual(['Control+Alt+Space','Control+Shift+Space']);
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
    await page.getByLabel('Global voice shortcut').fill('Control+Shift+Space');
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
    expect(core.diagnostics).toEqual([{code:'shortcut_registration_failed',shortcut:'Control+Alt+Space'}]);
    await expect(page.locator('#shortcut-error')).toContainText('Could not register');
    await expect(page.getByRole('alert')).toHaveCount(0);
    await expect(page.getByRole('log')).toContainText("shortcut couldn't be registered");
    await expect(page.getByRole('log')).toHaveAttribute('aria-live','off');
    await expect(page.getByRole('button',{name:'Start listening',exact:true})).toBeEnabled();
    await page.getByLabel('Global voice shortcut').fill('Control+Shift+Space');
    await page.getByRole('button',{name:'Apply shortcut',exact:true}).click();
    await expect(page.locator('#shortcut-error')).toContainText('Could not register Control+Shift+Space');
    expect(core.diagnostics).toHaveLength(1);
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
