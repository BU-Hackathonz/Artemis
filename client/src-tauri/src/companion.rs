use serde::{Deserialize, Serialize};
use std::sync::Mutex;
use tauri::{AppHandle, Manager, PhysicalPosition, State, WebviewWindow};

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Connection {
    #[default]
    Starting,
    Ready,
    Disconnected,
}
#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Task {
    #[default]
    Idle,
    Queued,
    Working,
    AwaitingReply,
    AwaitingApproval,
    Running,
    Succeeded,
    Failed,
    Canceled,
}
#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Microphone {
    #[default]
    Idle,
    Starting,
    Listening,
    Stopping,
    Error,
}
#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Status {
    connection: Connection,
    task: Task,
    microphone: Microphone,
    approval: bool,
    speech_ready: bool,
}
#[derive(Default)]
pub struct CompanionState(Mutex<Status>);

fn require(window: &WebviewWindow, label: &str) -> Result<(), String> {
    if window.label() == label {
        Ok(())
    } else {
        Err("This window cannot perform that action.".into())
    }
}
fn controls(app: &AppHandle, focus: bool) -> Result<(), String> {
    let main = app
        .get_webview_window("main")
        .ok_or("Artemis controls are unavailable.")?;
    main.show()
        .map_err(|_| "Could not show Artemis controls.")?;
    main.unminimize()
        .map_err(|_| "Could not restore Artemis controls.")?;
    if focus {
        main.set_focus()
            .map_err(|_| "Choose Artemis controls in the taskbar.")?;
    }
    if let Some(companion) = app.get_webview_window("companion") {
        let _ = companion.hide();
    }
    Ok(())
}
pub fn place(app: &AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("companion")
        .ok_or("Desktop status is unavailable.")?;
    let main = app
        .get_webview_window("main")
        .ok_or("Artemis controls are unavailable.")?;
    let monitor = main
        .current_monitor()
        .ok()
        .flatten()
        .or_else(|| app.primary_monitor().ok().flatten())
        .ok_or("No monitor is available.")?;
    let area = monitor.work_area();
    let scale = monitor.scale_factor();
    let width = 420.0_f64.min((area.size.width as f64 / scale - 24.0).max(240.0));
    window
        .set_size(tauri::LogicalSize::new(
            width,
            if width < 360.0 { 144.0 } else { 104.0 },
        ))
        .map_err(|_| "Could not size desktop status.")?;
    let physical_width = (width * scale) as i32;
    let x = area.position.x + (area.size.width as i32 - physical_width) / 2;
    let y = area.position.y + (12.0 * scale) as i32;
    window
        .set_position(PhysicalPosition::new(x, y))
        .map_err(|_| "Could not place desktop status.".into())
}
#[tauri::command]
pub fn get_companion_status(
    window: WebviewWindow,
    state: State<'_, CompanionState>,
) -> Result<Status, String> {
    require(&window, "companion")?;
    state
        .0
        .lock()
        .map(|s| s.clone())
        .map_err(|_| "Desktop status is unavailable.".into())
}
#[tauri::command]
pub fn update_companion_status(
    window: WebviewWindow,
    app: AppHandle,
    state: State<'_, CompanionState>,
    status: Status,
) -> Result<(), String> {
    require(&window, "main")?;
    let reveal = {
        let mut current = state
            .0
            .lock()
            .map_err(|_| "Desktop status is unavailable.")?;
        let silent_ready = matches!(status.connection, Connection::Ready)
            && !status.speech_ready
            && (!matches!(current.connection, Connection::Ready) || current.speech_ready);
        let disconnected = matches!(status.connection, Connection::Disconnected)
            && !matches!(current.connection, Connection::Disconnected);
        let reveal = (status.approval && !current.approval) || silent_ready || disconnected;
        *current = status;
        reveal
    };
    // Reveal the standard window; Windows may require Alt+Tab to give a background app focus.
    if reveal {
        controls(&app, false)?;
    }
    Ok(())
}
#[tauri::command]
pub fn show_main(window: WebviewWindow, app: AppHandle) -> Result<(), String> {
    require(&window, "companion")?;
    controls(&app, true)
}
#[tauri::command]
pub fn enter_compact(
    window: WebviewWindow,
    app: AppHandle,
    state: State<'_, CompanionState>,
) -> Result<(), String> {
    require(&window, "main")?;
    if state
        .0
        .lock()
        .map_err(|_| "Desktop status is unavailable.")?
        .approval
    {
        return Err("Resolve the pending approval before using compact status.".into());
    }
    place(&app)?;
    let companion = app
        .get_webview_window("companion")
        .ok_or("Desktop status is unavailable.")?;
    companion
        .show()
        .map_err(|_| "Could not show desktop status.")?;
    companion
        .set_focus()
        .map_err(|_| "Could not focus desktop status.")?;
    window
        .minimize()
        .map_err(|_| "Could not minimize Artemis controls.".into())
}
pub fn window_event(window: &tauri::Window, event: &tauri::WindowEvent) {
    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
        if window.label() == "companion" {
            api.prevent_close();
            let _ = controls(window.app_handle(), true);
        } else if window.label() == "main" {
            window.app_handle().exit(0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn status_accepts_fixed_values_and_rejects_arbitrary_data() {
        let valid = json!({"connection":"ready", "task":"working", "microphone":"idle", "approval":false, "speech_ready":true});
        assert!(serde_json::from_value::<Status>(valid.clone()).is_ok());
        let mut unexpected = valid.clone();
        unexpected["transcript"] = json!("must not cross this boundary");
        assert!(serde_json::from_value::<Status>(unexpected).is_err());
        let mut invalid = valid;
        invalid["task"] = json!("arbitrary speech");
        assert!(serde_json::from_value::<Status>(invalid).is_err());
    }

    #[test]
    fn compact_window_cannot_read_configuration_or_control_the_core() {
        let capability: serde_json::Value =
            serde_json::from_str(include_str!("../capabilities/companion.json")).unwrap();
        assert_eq!(capability["windows"], json!(["companion"]));
        assert_eq!(capability["local"], json!(true));
        assert_eq!(
            capability["permissions"],
            json!(["allow-get-companion-status", "allow-show-main"])
        );
    }
}
