use crate::companion::require_label;
use serde::{Deserialize, Serialize};
use std::sync::Mutex;
use tauri::{ipc::Channel, AppHandle, Manager, PhysicalPosition, State, WebviewWindow};

#[derive(Clone, Copy, Default, Deserialize, Serialize, PartialEq, Debug)]
#[serde(rename_all = "snake_case")]
pub enum View {
    #[default]
    Full,
    Settings,
}
#[derive(Clone, Serialize)]
pub struct Notice {
    revision: u32,
    view: View,
    guard: bool,
}
#[derive(Default)]
struct Pending {
    revision: u32,
    view: View,
    reveal: bool,
    focus: bool,
    guard: bool,
    handler: Option<Channel<Notice>>,
}
impl Pending {
    fn acknowledge(&mut self, revision: u32) -> Option<(View, bool)> {
        if self.revision != revision || !self.reveal {
            return None;
        }
        self.reveal = false;
        Some((self.view, self.focus))
    }
}
#[derive(Default)]
pub struct ControlsState(Mutex<Pending>);

pub fn request(app: &AppHandle, view: View, focus: bool, guard: bool) -> Result<(), String> {
    let state = app.state::<ControlsState>();
    let (handler, notice) = {
        let mut pending = state.0.lock().map_err(|_| "Controls unavailable.")?;
        pending.revision = pending.revision.wrapping_add(1);
        pending.view = view;
        pending.reveal = true;
        pending.focus = focus;
        pending.guard = guard;
        (
            pending.handler.clone(),
            Notice {
                revision: pending.revision,
                view,
                guard,
            },
        )
    };
    if let Some(handler) = handler {
        handler.send(notice).map_err(|_| "Controls unavailable.")?;
    }
    Ok(())
}
#[tauri::command]
pub fn register_controls_handler(
    window: WebviewWindow,
    state: State<'_, ControlsState>,
    handler: Channel<Notice>,
) -> Result<(), String> {
    require_label(window.label(), "main")?;
    let notice = {
        let mut pending = state.0.lock().map_err(|_| "Controls unavailable.")?;
        pending.handler = Some(handler.clone());
        Notice {
            revision: pending.revision,
            view: pending.view,
            guard: pending.guard,
        }
    };
    handler
        .send(notice)
        .map_err(|_| "Controls unavailable.".into())
}
#[tauri::command]
pub fn show_settings(window: WebviewWindow, app: AppHandle) -> Result<(), String> {
    require_label(window.label(), "companion")?;
    request(&app, View::Settings, true, false)
}
#[tauri::command]
pub fn set_controls_view(window: WebviewWindow, app: AppHandle, view: View) -> Result<(), String> {
    require_label(window.label(), "main")?;
    request(&app, view, true, true)
}
#[tauri::command]
pub fn controls_view_ready(
    window: WebviewWindow,
    state: State<'_, ControlsState>,
    revision: u32,
) -> Result<(), String> {
    require_label(window.label(), "main")?;
    let (view, focus) = {
        let mut pending = state.0.lock().map_err(|_| "Controls unavailable.")?;
        match pending.acknowledge(revision) {
            Some(request) => request,
            None => return Ok(()),
        }
    };
    place(&window, view)?;
    window.show().map_err(|_| "Could not show controls.")?;
    window
        .unminimize()
        .map_err(|_| "Could not restore controls.")?;
    if focus {
        window
            .set_focus()
            .map_err(|_| "Choose Artemis in the taskbar.")?;
    }
    Ok(())
}
fn place(window: &WebviewWindow, view: View) -> Result<(), String> {
    // Keep both controls views below the status strip, in the current monitor's work area.
    let monitor = window
        .current_monitor()
        .ok()
        .flatten()
        .or_else(|| window.app_handle().primary_monitor().ok().flatten())
        .ok_or("No monitor available.")?;
    let area = monitor.work_area();
    let scale = monitor.scale_factor();
    let available_width = area.size.width as f64 / scale;
    let available_height = area.size.height as f64 / scale;
    let inner = window
        .inner_size()
        .map_err(|_| "Could not measure controls.")?;
    let outer = window
        .outer_size()
        .map_err(|_| "Could not measure controls.")?;
    let chrome_width = outer.width.saturating_sub(inner.width) as f64 / scale;
    let chrome_height = outer.height.saturating_sub(inner.height) as f64 / scale;
    let width = (if view == View::Settings {
        420.0_f64
    } else {
        680.0_f64
    })
    .min((available_width - 24.0 - chrome_width).max(1.0));
    let height = (if view == View::Settings {
        520.0_f64
    } else {
        680.0_f64
    })
    .min((available_height - 84.0 - chrome_height).max(1.0));
    window
        .set_min_size(Some(tauri::LogicalSize::new(
            320.0_f64.min(width),
            240.0_f64.min(height),
        )))
        .map_err(|_| "Could not size controls.")?;
    window
        .set_size(tauri::LogicalSize::new(width, height))
        .map_err(|_| "Could not size controls.")?;
    let x = area.position.x + ((area.size.width as f64 - width * scale) / 2.0) as i32;
    let y = area.position.y + (72.0 * scale) as i32;
    window
        .set_position(PhysicalPosition::new(x, y))
        .map_err(|_| "Could not position controls.".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn startup_and_stale_acknowledgements_never_reveal_a_window() {
        let mut pending = Pending::default();
        assert!(pending.acknowledge(0).is_none());
        pending.revision = 2;
        pending.view = View::Settings;
        pending.reveal = true;
        pending.focus = true;
        assert!(pending.acknowledge(1).is_none());
        assert!(pending.reveal);
        assert_eq!(pending.acknowledge(2), Some((View::Settings, true)));
        assert!(pending.acknowledge(2).is_none());
    }
    #[test]
    fn views_are_closed_and_never_accept_arbitrary_commands() {
        assert_eq!(
            serde_json::from_str::<View>("\"settings\"").unwrap(),
            View::Settings
        );
        assert!(serde_json::from_str::<View>("\"hide-main\"").is_err());
    }
}
