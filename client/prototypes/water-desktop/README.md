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

## Try it (standalone prototype, no core)

```sh
./install.sh            # install + enable (log out and back in on Wayland)
./install.sh --remove   # uninstall
```

- Click the chevron or the waveform: a panel opens. Type what you want (e.g. "tour the screen")
  and press Enter, or pick a command. Escape closes it.
- While a command runs, the ring shines, the bars move, and the ring flies out as a droplet through
  the steps. When a command types, the droplet rides the text cursor (the app's real caret via
  AT-SPI when available) and its trail follows the lines. Then it returns to its slot.
- Commands only type into windows they opened themselves, and stop if anything else takes the
  keyboard. "Stop" in the panel cancels.
- Settings (`gnome-extensions prefs artemis-water@bu-hackathonz.github.io`): agent look
  (island, liquid ring, glass orb), font, size, motion, and the older pointer-driven effects.

## Limits

No microphone (the waveform opens the panel), and typed requests match the built-in commands;
real requests would go through the Artemis core. The glass refracts the desktop through GNOME's
compositor, which the Windows app can't do.

## Files

`extension.js` (wiring), `island.js` + `stylesheet.css` (the island), `caret.js` (cursor tracking), `presets.js` (commands),
`ringEffect.js` / `orbEffect.js` / `lensEffect.js` / `waterEffect.js` (shaders), `prefs.js`,
`schemas/`.
