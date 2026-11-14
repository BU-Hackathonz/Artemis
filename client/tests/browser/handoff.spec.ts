import {test,expect} from '@playwright/test';
import {createServer,type ServerResponse} from 'node:http';

test('late typed response cannot replace a newer active task or block the next request',async({page})=>{
  let events:ServerResponse|undefined;
  let pending:ServerResponse|undefined;
  const canceled:string[]=[];
  let seq=0;
  const emit=(taskId:string,state:string)=>events!.write(`id: ${++seq}\ndata: ${JSON.stringify({seq,session_id:'ses_handoff',task_id:taskId,at:new Date().toISOString(),type:'task.state',data:{state},announce:null})}\n\n`);
  const server=createServer((req,res)=>{
    res.setHeader('access-control-allow-origin','http://127.0.0.1:1420');
    res.setHeader('access-control-allow-headers','Authorization,Content-Type,Last-Event-ID');
    res.setHeader('access-control-allow-methods','GET,POST,OPTIONS');
    if(req.method==='OPTIONS'){res.writeHead(204).end();return;}
    if(req.headers.authorization!=='Bearer handoff-test'){res.writeHead(401).end();return;}
    if(req.url==='/sessions/ses_handoff/events'){events=res;res.writeHead(200,{'content-type':'text/event-stream'});res.write(': connected\n\n');return;}
    if(req.url==='/sessions/ses_handoff/tasks'){
      pending=res;emit('tsk_a','succeeded');emit('tsk_b','working');return;
    }
    if(req.url?.endsWith('/cancel')){
      const tid=req.url.split('/')[2];canceled.push(tid);
      if(tid==='tsk_b'){emit('tsk_b','canceled');res.writeHead(204).end();}else res.writeHead(404).end();
      return;
    }
    res.setHeader('content-type','application/json');
    if(req.url==='/health')res.end(JSON.stringify({ok:true,version:'handoff-fixture',voice_ready:false,screen_reader:null}));
    else if(req.url==='/sessions')res.end(JSON.stringify({session_id:'ses_handoff'}));
    else res.writeHead(404).end();
  });
  await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{
    await page.goto('/');
    await page.getByLabel('Core address').fill(`http://127.0.0.1:${(server.address() as {port:number}).port}`);
    await page.getByLabel('Launch token').fill('handoff-test');
    await page.getByRole('button',{name:'Connect to core',exact:true}).click();
    await expect(page.getByRole('status')).toHaveText('Connected');
    const input=page.getByLabel('What would you like to build?');await input.fill('first request');await page.keyboard.press('Enter');
    await expect(page.getByText('Task: working',{exact:true})).toBeVisible();
    pending!.setHeader('content-type','application/json');pending!.end(JSON.stringify({task_id:'tsk_a'}));
    const cancel=page.getByRole('button',{name:'Cancel task',exact:true});await expect(cancel).toBeEnabled();await cancel.click();
    await expect.poll(()=>canceled).toEqual(['tsk_b']);
    await expect(page.getByText('Task: canceled',{exact:true})).toBeVisible();
    await input.fill('next typed request');
    await expect(page.getByRole('button',{name:'Send request',exact:true})).toBeEnabled();
  }finally{server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}
});
