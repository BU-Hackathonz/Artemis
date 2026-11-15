# Artemis desktop agent (GNOME prototype)

A GNOME Shell extension (GNOME 46 and 48–50; 46 is Ubuntu 24.04, the demo laptop) for trying the Artemis agent on a Linux desktop. Its default
look is the **top-edge island**: a small frosted pill under the top bar with the glassy liquid ring,
voice bars and a chevron. This is a design prototype; the Windows app is in `client/src-tauri`.

## Run on Ubuntu with the Artemis core

The island talks to the Artemis core. Start the core, and the island connects by itself.

```sh
bash infra/setup-ubuntu.sh                 # once: apt, uv, dependencies, Whisper model
client/prototypes/water-desktop/install.sh # copy the extension into your profile
# On Wayland: log out and back in, then:
gnome-extensions enable artemis-water@bu-hackathonz.github.io
infra/start.sh --voice stub                # start the core; type requests in the island
```

- The island connects when `infra/start.sh` writes `$XDG_RUNTIME_DIR/artemis/launch.json`. You do
  not need to restart GNOME Shell. With no core, the island stays idle and Orca reads "Artemis core
  is not running".
- Type a request: open the island with the chevron or the waveform, type, and press Enter.
- Push-to-talk: hold Control and Shift, press Space, speak, then release both.
- Approvals open a GNOME dialog. Enter approves and Escape rejects. A blocked action starts on Reject
  and needs Shift+Enter to approve.
- The full steps, the eight island states and troubleshooting are in [`infra/README.md`](../../../infra/README.md#ubuntu-run-the-core-and-the-gnome-island).

The scripted demo presets and the virtual keyboard typing are removed from the connected build. The
sections below describe the standalone prototype.

## Install

```sh
./install.sh            # install + enable (log out and back in on Wayland)
./install.sh --remove   # uninstall
```

Without a core the island stays idle and says "Artemis core is not running". The scripted demo
commands and the virtual-keyboard typing of the first prototype are gone (they are in the git
history, commit 835916c).

Settings (`gnome-extensions prefs artemis-water@bu-hackathonz.github.io`): agent look (island,
liquid ring, glass orb), font, size, motion, and the older pointer-driven effects. The push-to-talk
shortcut is the `push-to-talk` key (`gsettings` path `org.gnome.shell.extensions.artemis-water`).

## Checks without GNOME Shell

`core.js` and `islandState.js` import no Shell modules, so they run under plain `gjs` (1.80 on
Ubuntu 24.04) against a running core:

```sh
gjs -m tests/island-states-gjs.js approve   # or reject; uses $XDG_RUNTIME_DIR/artemis/launch.json
ARTEMIS_TEST_URL=http://127.0.0.1:PORT ARTEMIS_TEST_TOKEN=... gjs -m tests/core-gjs-test.js
```

The pure modules (`islandState.js`, `approvalKeys.js`, `ringLook.js`) also have vitest tests in
`client/tests/`, run by CI.

## Files

`extension.js` (wiring), `core.js` (the connection to the core), `islandState.js` (the 8 states,
contracts.md §8), `island.js` + `stylesheet.css` (the island), `approvalDialog.js` +
`approvalKeys.js` (approvals and the Enter guard), `ringLook.js` (the ring per state),
`ringEffect.js` / `orbEffect.js` / `lensEffect.js` / `waterEffect.js` (shaders), `prefs.js`,
`schemas/`.
