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
