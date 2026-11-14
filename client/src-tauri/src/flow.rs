//! The flow overlay: a transparent, click-through, always-on-top window over the primary work area
//! that shows the droplet while Artemis works. Decoration only.
//!
//! Least privilege, like the compact notch: the window has no `core:window` or `core:event`
//! permission at all. It is placed, made click-through and sized here in Rust, it reads fixed
//! values through caller-guarded commands (label must be "flow"; the label comes from the invoking
//! window, never an argument), and it can only show or hide itself.
//!
//! Opt-in with the notch (start.ps1 -Compact): tauri.conf.json declares the window with
//! `create: false`, and it is created only next to the compact status window.

use serde::Serialize;
use tauri::{AppHandle, Manager, PhysicalPosition, PhysicalSize, State, WebviewWindow};

use crate::companion::{require_label, CompanionState};

/// Where the notch's ring sits inside the compact window (logical px), from the notch's own CSS:
/// the pill is 216 px wide, centred; the ring's centre is 21 px from the pill's left edge and 24 px
/// below the top of the 48 px window.
const PILL_WIDTH: f64 = 216.0;
const RING_FROM_PILL_LEFT: f64 = 21.0;
const RING_FROM_TOP: f64 = 24.0;

/// Fixed values only: whether Artemis is working, whether an approval waits, where the ring is.
#[derive(Serialize)]
pub struct FlowState {
    busy: bool,
    approval: bool,
    /// The ring's centre in the flow window's CSS pixels, or None while the notch is not showing.
    ring: Option<(f64, f64)>,
    /// The flow window's top-left on the screen and its scale, so the page can convert the text
    /// caret's physical screen position into its own pixels.
    origin: (i32, i32),
    scale: f64,
}

pub fn ring_centre(
    companion: (i32, i32),
    companion_width: f64,
    origin: (i32, i32),
    scale: f64,
) -> (f64, f64) {
    let left = ((companion_width - PILL_WIDTH) / 2.0).max(0.0);
    (
        (companion.0 - origin.0) as f64 / scale + left + RING_FROM_PILL_LEFT,
        (companion.1 - origin.1) as f64 / scale + RING_FROM_TOP,
    )
}

/// Create the overlay, place it over the primary work area and make it click-through. None when
/// the window is not declared or cannot be built; the notch works without it.
pub fn create(app: &AppHandle) -> Option<WebviewWindow> {
    let config = app
        .config()
        .app
        .windows
        .iter()
        .find(|window| window.label == "flow")?
        .clone();
    let window = tauri::WebviewWindowBuilder::from_config(app, &config)
        .ok()?
        .build()
        .ok()?;
    place(app, &window).ok()?;
    window.set_ignore_cursor_events(true).ok()?;
    Some(window)
}

fn place(app: &AppHandle, window: &WebviewWindow) -> Result<(), String> {
    let monitor = app
        .primary_monitor()
        .ok()
        .flatten()
        .ok_or("No monitor is available.")?;
    let area = monitor.work_area();
    window
        .set_position(PhysicalPosition::new(area.position.x, area.position.y))
        .map_err(|_| "Could not place the flow overlay.")?;
    window
        .set_size(PhysicalSize::new(area.size.width, area.size.height))
        .map_err(|_| "Could not size the flow overlay.".into())
}

#[tauri::command]
pub fn get_flow_state(
    window: WebviewWindow,
    app: AppHandle,
    state: State<'_, CompanionState>,
) -> Result<FlowState, String> {
    require_label(window.label(), "flow")?;
    let (busy, approval) = state.flow_snapshot()?;
    let origin = window
        .outer_position()
        .map(|p| (p.x, p.y))
        .unwrap_or((0, 0));
    let scale = window.scale_factor().unwrap_or(1.0);
    let ring = app
        .get_webview_window("companion")
        .filter(|companion| companion.is_visible().unwrap_or(false))
        .and_then(|companion| {
            let position = companion.outer_position().ok()?;
            let width = companion.outer_size().ok()?.width as f64 / scale;
            Some(ring_centre((position.x, position.y), width, origin, scale))
        });
    Ok(FlowState {
        busy,
        approval,
        ring,
        origin,
        scale,
    })
}

/// Where the text caret is (physical screen pixels), or None. Two numbers, never any text or
/// window or application name. See caret.rs for what it can and cannot see.
#[tauri::command]
pub fn get_caret_position(window: WebviewWindow) -> Result<Option<(i32, i32)>, String> {
    require_label(window.label(), "flow")?;
    Ok(crate::caret::position())
}

/// The overlay shows and hides itself, and nothing else.
#[tauri::command]
pub fn set_flow_visible(window: WebviewWindow, visible: bool) -> Result<(), String> {
    require_label(window.label(), "flow")?;
    let result = if visible { window.show() } else { window.hide() };
    result.map_err(|_| "Could not change the flow overlay.".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_ring_is_found_from_the_notchs_position_and_the_overlay_origin() {
        // A 232 px wide notch window at screen (800, 12), overlay at the screen origin, scale 1.
        let (x, y) = ring_centre((800, 12), 232.0, (0, 0), 1.0);
        assert_eq!(x, 800.0 + 8.0 + 21.0);
        assert_eq!(y, 12.0 + 24.0);
    }

    #[test]
    fn it_converts_physical_pixels_with_the_scale_and_the_overlay_origin() {
        // 125 percent scale: 1000 physical px right of the overlay origin is 800 css px.
        let (x, y) = ring_centre((1056, 15), 232.0, (56, 0), 1.25);
        assert_eq!(x, 800.0 + 8.0 + 21.0);
        assert_eq!(y, 12.0 + 24.0);
    }

    #[test]
    fn a_window_narrower_than_the_pill_never_pushes_the_ring_left() {
        let (x, _) = ring_centre((0, 0), 100.0, (0, 0), 1.0);
        assert_eq!(x, RING_FROM_PILL_LEFT);
    }

    #[test]
    fn only_the_flow_window_may_ask() {
        assert!(require_label("flow", "flow").is_ok());
        for other in ["main", "companion", "agent", ""] {
            assert!(require_label(other, "flow").is_err());
        }
    }
}
