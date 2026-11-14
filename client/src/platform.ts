import { invoke } from '@tauri-apps/api/core';

export interface NativeCoreConfig { core_url: string; token: string }
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

/** What the desktop agent ring shows (sent from the main window to the agent window). */
export interface AgentStatus {connected: boolean; taskState: string; microphone: string; approval: boolean; kick: number}
// Events between the main window and the desktop agent window (labels "main" and "agent").
const AGENT = {status:'artemis:agent-status', ready:'artemis:agent-ready', open:'artemis:agent-open', visible:'artemis:agent-visible', reset:'artemis:agent-reset'};
type Unlisten = () => void;
async function on<T>(event: string, handler: (payload: T) => void): Promise<Unlisten> {
  const {listen} = await import('@tauri-apps/api/event');
  return listen<T>(event, e => handler(e.payload));
}
async function sendTo(target: string, event: string, payload?: unknown) {
  const {emitTo} = await import('@tauri-apps/api/event');
  await emitTo(target, event, payload);
}
export const platform = {
  name: native ? 'Desktop' : 'Browser',
  native,
  /** Main-window side of the desktop agent ring (desktop app only; no-ops in the browser). */
  agent: {
    // the island ('agent') and the flow overlay ('flow') both picture the state
    sendStatus(status: AgentStatus): Promise<void> { return native ? Promise.all([sendTo('agent', AGENT.status, status), sendTo('flow', AGENT.status, status)]).then(() => {}) : Promise.resolve(); },
    setVisible(visible: boolean): Promise<void> { return native ? Promise.all([sendTo('agent', AGENT.visible, visible), sendTo('flow', AGENT.visible, visible)]).then(() => {}) : Promise.resolve(); },
    resetPosition(): Promise<void> { return native ? sendTo('agent', AGENT.reset) : Promise.resolve(); },
    onReady(handler: () => void): Promise<Unlisten> { return native ? on(AGENT.ready, handler) : Promise.resolve(() => {}); },
    /** The agent was clicked: bring the controls back and focus them. */
    onOpen(handler: () => void): Promise<Unlisten> {
      if (!native) return Promise.resolve(() => {});
      return on(AGENT.open, async () => {
        const {getCurrentWindow} = await import('@tauri-apps/api/window');
        const main = getCurrentWindow();
        await main.unminimize(); await main.show(); await main.setFocus();
        handler();
      });
    },
  },
  /** Agent-window side (the transparent ring window). */
  agentWindow: {
    onStatus(handler: (status: AgentStatus) => void): Promise<Unlisten> { return on(AGENT.status, handler); },
    onVisible(handler: (visible: boolean) => void): Promise<Unlisten> { return on(AGENT.visible, handler); },
    onReset(handler: () => void): Promise<Unlisten> { return on(AGENT.reset, handler); },
    ready(): Promise<void> { return sendTo('main', AGENT.ready); },
    open(): Promise<void> { return sendTo('main', AGENT.open); },
  },
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
  getCoreConfig(): Promise<NativeCoreConfig> {
    if (!native) return Promise.reject(new Error(launchError));
    // The native state reads env once; also invoke only once per page load.
    configuration ??= invoke<unknown>('get_core_config').then(value => {
      if (!value || typeof value !== 'object' || !('core_url' in value) || typeof value.core_url !== 'string' || !('token' in value) || typeof value.token !== 'string') throw new Error(launchError);
      return {core_url:value.core_url, token:value.token};
    }).catch(() => { throw new Error(launchError); });
    return configuration;
  },
};
