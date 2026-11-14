import { invoke } from '@tauri-apps/api/core';

export interface NativeCoreConfig { core_url: string; token: string }
const native = '__TAURI_INTERNALS__' in window;
const launchError = 'Native core configuration is unavailable. Close Artemis and start it with infra/start.ps1.';
let configuration: Promise<NativeCoreConfig> | undefined;
export const platform = {
  name: native ? 'Desktop' : 'Browser',
  native,
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
