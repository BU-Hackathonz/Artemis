import {test, expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('settings is acknowledged after rendering, keeps typed requests in full view, and Escape restores controls without a compact launch', async ({page}) => {
  await page.addInitScript(() => {
    let handler: {onmessage: (notice: {revision:number; view:string}) => void} | undefined;
    let revision = 0;
    const calls: string[] = [];
    Object.defineProperty(window, 'settingsCalls', {value:calls});
    Object.defineProperty(window, '__TAURI_INTERNALS__', {value:{transformCallback:()=>1, unregisterCallback:()=>{}, invoke:async (command:string, args?: {handler:typeof handler; view:string}) => {
      calls.push(command);
      if (command === 'register_controls_handler') {handler=args?.handler;handler?.onmessage({revision,view:'full'});return;}
      if (command === 'set_controls_view') {handler?.onmessage({revision:++revision,view:args!.view});return;}
      if (command === 'controls_view_ready') {
        const heading=document.querySelector('.settings-panel h1');
        calls.push(heading ? `ready:${heading.textContent}:${document.activeElement === heading}` : 'ready:full');return;
      }
      if (command === 'get_core_config') throw new Error('fixture: no launcher');
      if (command.endsWith('is_registered')) return false;
      return;
    }}});
  });
  await page.goto('/');
  await page.getByRole('button', {name:'Settings and controls',exact:true}).click();
  await expect(page.getByRole('heading', {name:'Settings and controls'})).toBeFocused();
  await expect(page.getByRole('textbox')).toHaveCount(0);
  await expect(page.getByRole('button', {name:'Open request and activity controls'})).toBeVisible();
  await expect.poll(()=>page.evaluate(()=>(window as unknown as {settingsCalls:string[]}).settingsCalls)).toContain('ready:Artemis:true');
  expect((await new AxeBuilder({page}).analyze()).violations).toEqual([]);
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', {name:'Open request and activity controls'})).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', {name:'Artemis controls'})).toBeVisible();
  await page.getByRole('button', {name:'Settings and controls',exact:true}).click();
  // Without a compact launch Escape returns to the full view, keeping a recovery path.
  await expect(page.getByRole('heading', {name:'Settings and controls'})).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('heading', {name:'Artemis controls'})).toBeVisible();
});
