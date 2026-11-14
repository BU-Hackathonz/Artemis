import {test,expect} from '@playwright/test';
import {createServer,type ServerResponse} from 'node:http';

test('reconnect restores an empty focus without stealing deliberate log focus',async({page})=>{
  let events:ServerResponse|undefined;
  const server=createServer((req,res)=>{
    res.setHeader('access-control-allow-origin','http://127.0.0.1:1420');
    res.setHeader('access-control-allow-headers','Authorization,Content-Type,Last-Event-ID');
    res.setHeader('access-control-allow-methods','GET,POST,OPTIONS');
    if(req.method==='OPTIONS'){res.writeHead(204).end();return;}
    if(req.headers.authorization!=='Bearer focus-test'){res.writeHead(401).end();return;}
    if(req.url==='/sessions/ses_focus/events'){events=res;res.writeHead(200,{'content-type':'text/event-stream'});res.write(': connected\n\n');return;}
    res.setHeader('content-type','application/json');
    if(req.url==='/health')res.end(JSON.stringify({ok:true,version:'focus-fixture',voice_ready:false,screen_reader:null}));
    else if(req.url==='/sessions')res.end(JSON.stringify({session_id:'ses_focus'}));
    else res.writeHead(404).end();
  });
  await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{
    await page.goto('/');
    await page.getByLabel('Core address').fill(`http://127.0.0.1:${(server.address() as {port:number}).port}`);
    await page.getByLabel('Launch token').fill('focus-test');
    await page.getByRole('button',{name:'Connect to core',exact:true}).click();
    const request=page.getByLabel('What would you like to build?');
    await expect(request).toBeFocused();
    events!.end();
    await expect(request).toBeDisabled();
    await expect(request).not.toBeFocused();
    await expect(page.getByRole('status')).toHaveText('Connected');
    await expect(request).toBeFocused();
    const log=page.getByRole('log');await log.focus();
    events!.end();
    await expect(request).toBeDisabled();
    await expect(page.getByRole('status')).toHaveText('Connected');
    await expect(log).toBeFocused();
  }finally{server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}
});
