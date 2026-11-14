# Artemis desktop agent (GNOME prototype)

A GNOME Shell extension (GNOME 48–50) for trying the Artemis agent on a Linux desktop. Its default
look is the **top-edge island**: a small pill under the top bar with the glassy ring, "artemis.",
a waveform and a chevron. This is a design prototype; the Windows app is in `client/src-tauri`.

## Try it

```sh
./install.sh            # install + enable (log out and back in on Wayland)
./install.sh --remove   # uninstall
```

- Click the chevron or the waveform: a panel opens. Type what you want (e.g. "tour the screen")
  and press Enter, or pick a command. Escape closes it.
- While a command runs, the pill says "Working · …" and a droplet flies out of the ring through
  the steps, leaving a short trail, then returns. "Done" shows briefly afterwards.
- Commands only type into windows they opened themselves, and stop if anything else takes the
  keyboard. "Stop" in the panel cancels.
- Settings (`gnome-extensions prefs artemis-water@bu-hackathonz.github.io`): agent look
  (island, liquid ring, glass orb), size, motion, and the older pointer-driven effects.

## Limits

No microphone (the waveform opens the panel), and typed requests match the built-in commands;
real requests would go through the Artemis core. The glass refracts the desktop through GNOME's
compositor, which the Windows app can't do.

## Files

`extension.js` (wiring), `island.js` + `stylesheet.css` (the island), `presets.js` (commands),
`ringEffect.js` / `orbEffect.js` / `lensEffect.js` / `waterEffect.js` (shaders), `prefs.js`,
`schemas/`.
