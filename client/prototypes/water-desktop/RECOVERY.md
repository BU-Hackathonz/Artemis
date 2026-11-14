# Original Ubuntu island recovery

This directory restores the original GNOME prototype byte for byte from
`client/desktop-island` at `f8bb1c86412009aa071a33eb1b07b0848903e243` (PR #80).
No original source, styling, settings defaults or saved captures have been changed.
The current Artemis core and Windows client are retained.

## Baseline evidence

- `captures/28-island-calmer.png`: saved idle and working island screenshots, inspected during recovery.
- `captures/26-top-edge-island-states.png`: earlier saved state comparison.
- History: `d3324ee` introduces the top-edge island; `686f157` calms the island and follows the caret; `f8bb1c8` adjusts ball visibility while typing.

These are historical captures, not proof of a current Ubuntu run. Jun must confirm that this
source/capture is the intended original demo before it becomes the integration baseline.

## Runtime inventory

The extension metadata lists GNOME Shell 48, 49 and 50. Required runtime components include
GNOME Shell/GJS (Clutter, St, Shell, Meta, Gio and GLib), AT-SPI introspection for caret tracking,
and GTK/libadwaita for the preferences window. The installer requires Bash,
`glib-compile-schemas`, and `gnome-extensions`. Confirm availability on the demo laptop;
do not disable GNOME version validation or assume another Shell version works.

On the Ubuntu desktop, collect the environment first:

```sh
. /etc/os-release
printf '%s\n' "$PRETTY_NAME"
gnome-shell --version
printf '%s\n' "$XDG_SESSION_TYPE"
command -v gjs glib-compile-schemas gnome-extensions
```

Then reproduce the original installation from this directory:

```sh
./install.sh
# On Wayland: log out and back in to discover/reload the extension.
gnome-extensions info artemis-water@bu-hackathonz.github.io
# If installation did not enable it:
gnome-extensions enable artemis-water@bu-hackathonz.github.io
# Existing settings:
gnome-extensions prefs artemis-water@bu-hackathonz.github.io
```

Record the idle island before trying a built-in command, and compare it with the saved capture.
Record desktop scale and any pre-existing extension settings; do not reset a user's settings
without their agreement. Run the built-in prototype only as an explicitly labeled baseline
check. The original README describes its commands and limitations. Removal uses
`./install.sh --remove` and removes this extension's per-user installed directory.

## Integration boundary

This restored prototype has **no microphone, no STT, no live Artemis core connection, and no
ElevenLabs narration**. Its CommandRunner runs built-in demonstrations. Do not present those
as current agent/core work or infer task progress from their timers.

David's latest directive requires Settings to be the only expandable general-purpose UI.
The original command-entry popup therefore remains historical baseline behavior, not an
approved integrated product surface. After the original appearance is confirmed on hardware,
replace the prototype command driver with a thin adapter to the current authenticated core.
Preserve the original island appearance, settings and restrained state rendering. Retain
keyboard-accessible safety approvals and a nonvisual path to inspect code, results and errors.
Never place credentials in extension source, screenshots, logs, or versioned settings.

Voice/core integration remains with Avery's accepted lane; coordinate credential transport,
real event mapping, cancellation and approval handling before changing those interfaces.
On Ubuntu, narration and Orca are separate concerns. Do not port NVDA-specific routing as a
Linux screen-reader implementation.

## Verification status and remaining gates

Recovery was prepared on macOS, which cannot run GNOME Shell. Source identity, JavaScript
syntax, installer syntax and schema validation can be checked here; Ubuntu runtime behavior
cannot. Required next evidence: confirmation of the intended baseline; actual Ubuntu/GNOME
version; faithful idle/working baseline recording; then live core, microphone/STT and audio
integration and repeated end-to-end requests with accurate state, approval, cancellation,
result/error and recovery behavior. No hardware or end-to-end pass is claimed by this restore.
