// Keep native calls here. The first keyboard skeleton intentionally has no native privileges.
// Global push-to-talk and launch-token injection are integration work with infra/voice owners.
export const platform = {
  name: '__TAURI_INTERNALS__' in window ? 'Desktop' : 'Browser',
};
