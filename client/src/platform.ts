import type {CompanionStatus} from './desktopStatus';
import { Channel, invoke } from '@tauri-apps/api/core';

// compact: the launcher opted in to the compact desktop status (infra/start.ps1 -Compact).
export interface NativeCoreConfig { core_url: string; token: string; compact: boolean }
/** What the flow overlay may know (fixed values): working, approval waiting, where the notch ring is
 * (the overlay's CSS pixels, null while the notch is hidden), and the overlay's origin and scale. */
export interface FlowState { busy: boolean; approval: boolean; ring: [number, number] | null; origin: [number, number]; scale: number }
const native = '__TAURI_INTERNALS__' in window;
const launchError = 'Native core configuration is unavailable. Close Artemis and start it with infra/start.ps1.';
let registrationQueue: Promise<unknown> = Promise.resolve();
const shortcutOwners = new Map<string, number>();
let shortcutGeneration = 0;
function serialShortcut<T>(action: () => Promise<T>): Promise<T> {
  const result = registrationQueue.then(action, action);
  registrationQueue = result.catch(() => {});
  return result;
}
let configuration: Promise<NativeCoreConfig> | undefined;
export const platform = {
  name: native ? 'Desktop' : 'Browser',
  native,
  async registerPushToTalk(shortcut: string, handler: (state: 'Pressed' | 'Released') => void): Promise<() => Promise<void>> {
    if (!native) throw new Error('Global voice shortcut requires the desktop app.');
    const {register, unregister, isRegistered} = await import('@tauri-apps/plugin-global-shortcut');
    const generation = ++shortcutGeneration;
    await serialShortcut(async () => {
      if (await isRegistered(shortcut)) await unregister(shortcut);
      shortcutOwners.delete(shortcut);
      await register(shortcut, event => handler(event.state));
      shortcutOwners.set(shortcut, generation);
    });
    return () => serialShortcut(async () => {
      if (shortcutOwners.get(shortcut) !== generation) return;
      shortcutOwners.delete(shortcut);
      await unregister(shortcut);
    });
  },
  updateCompanionStatus(status: CompanionStatus, approvalChanged: boolean): Promise<boolean> {return native ? invoke<boolean>('update_companion_status', {status, approvalChanged}) : Promise.resolve(false);},
  getCompanionStatus(): Promise<CompanionStatus> {return invoke('get_companion_status');},
  /** Flow overlay only (the native command checks its caller): fixed values for the droplet. */
  getFlowState(): Promise<FlowState> {return invoke('get_flow_state');},
  setFlowVisible(visible: boolean): Promise<void> {return invoke('set_flow_visible', {visible});},
  /** The text caret's screen position in physical pixels, or null when the focused application has
   * no system caret to follow. Flow overlay only; two numbers, never text. */
  async caretPosition(): Promise<{x: number; y: number} | null> {
    if (!native) return null;
    try {
      const at = await invoke<[number, number] | null>('get_caret_position');
      return Array.isArray(at) && at.length === 2 && at.every(Number.isFinite) ? {x: at[0], y: at[1]} : null;
    } catch { return null; }
  },
  enterCompact(): Promise<void> {return invoke('enter_compact');},
  revealApproval(): Promise<void> {return invoke('reveal_approval');},
  registerControlsHandler(handler: (notice: {revision: number; view: 'full' | 'settings'; guard: boolean}) => void): Promise<void> {
    const channel = new Channel<{revision: number; view: 'full' | 'settings'; guard: boolean}>();
    channel.onmessage = handler;
    return invoke('register_controls_handler', {handler: channel});
  },
  showSettings(): Promise<void> {return invoke('show_settings');},
  setControlsView(view: 'full' | 'settings'): Promise<void> {return invoke('set_controls_view', {view});},
  controlsViewReady(revision: number): Promise<void> {return invoke('controls_view_ready', {revision});},
  showMain(): Promise<void> {return invoke('show_main');},
  getCoreConfig(): Promise<NativeCoreConfig> {
    if (!native) return Promise.reject(new Error(launchError));
    // The native state reads env once; also invoke only once per page load.
    configuration ??= invoke<unknown>('get_core_config').then(value => {
      if (!value || typeof value !== 'object' || !('core_url' in value) || typeof value.core_url !== 'string' || !('token' in value) || typeof value.token !== 'string') throw new Error(launchError);
      return {core_url:value.core_url, token:value.token, compact:'compact' in value && value.compact === true};
    }).catch(() => { throw new Error(launchError); });
    return configuration;
  },
};
