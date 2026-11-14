#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use tauri::Manager;
mod companion;
mod controls;

#[derive(Clone, Serialize)]
struct CoreConfig {
    core_url: String,
    token: String,
    // Opt-in compact desktop status (infra/start.ps1 -Compact). Off: full controls only, as before.
    compact: bool,
}

const LAUNCH_ERROR: &str = "Native core configuration is missing or invalid. Close Artemis and start it with infra/start.ps1.";

impl CoreConfig {
    fn from_values(address: Option<String>, token: Option<String>) -> Result<Self, String> {
        let address = address.ok_or(LAUNCH_ERROR)?;
        let token = token.ok_or(LAUNCH_ERROR)?;
        if token.is_empty() || !token.bytes().all(|b| b.is_ascii_graphic()) {
            return Err(LAUNCH_ERROR.into());
        }
        let url = url::Url::parse(&address).map_err(|_| LAUNCH_ERROR)?;
        if url.scheme() != "http"
            || url.host_str() != Some("127.0.0.1")
            || !url.username().is_empty()
            || url.password().is_some()
            || url.path() != "/"
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err(LAUNCH_ERROR.into());
        }
        Ok(Self {
            core_url: url.origin().ascii_serialization(),
            token,
            compact: false,
        })
    }

    fn with_compact(self, compact: bool) -> Self {
        Self { compact, ..self }
    }

    fn from_env() -> Result<Self, String> {
        // Read exactly once at process start. Never log or derive Debug for these values.
        Self::from_values(
            std::env::var("ARTEMIS_CORE_URL").ok(),
            std::env::var("ARTEMIS_TOKEN").ok(),
        )
    }
}

// ARTEMIS_COMPACT=1 (start.ps1 -Compact) turns on the compact status window. It is not a secret.
fn compact_requested(value: Option<String>) -> bool {
    value.as_deref() == Some("1")
}

struct LaunchState(Result<CoreConfig, String>);

fn config_for_window(label: &str, state: &LaunchState) -> Result<CoreConfig, String> {
    companion::require_label(label, "main")?;
    state.0.clone()
}

#[tauri::command]
fn get_core_config(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, LaunchState>,
) -> Result<CoreConfig, String> {
    config_for_window(window.label(), state.inner())
}

fn main() {
    let compact = compact_requested(std::env::var("ARTEMIS_COMPACT").ok());
    let state = LaunchState(CoreConfig::from_env().map(|config| config.with_compact(compact)));
    // A failed launch shows full controls with the recovery text, never the compact status.
    let compact = state.0.as_ref().is_ok_and(|config| config.compact);
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(state)
        .manage(companion::CompanionState::default())
        .manage(controls::ControlsState::default())
        .setup(move |app| {
            // Default: no compact status window; full controls open and take focus as before.
            let status = if compact {
                companion::create(app.handle())
            } else {
                None
            };
            if let Some(main) = app.get_webview_window("main") {
                // Keep full controls in the taskbar/Alt+Tab even before they are first opened.
                if status.is_some() {
                    let _ = main.minimize();
                }
                let _ = main.show();
                if status.is_none() {
                    let _ = main.set_focus();
                }
            }
            if let Some(status) = status {
                if status.show().is_ok() {
                    let _ = status.set_focus();
                }
            }
            Ok(())
        })
        .on_window_event(companion::window_event)
        .invoke_handler(tauri::generate_handler![
            get_core_config,
            companion::get_companion_status,
            companion::update_companion_status,
            companion::show_main,
            companion::enter_compact,
            companion::reveal_approval,
            controls::register_controls_handler,
            controls::show_settings,
            controls::set_controls_view,
            controls::controls_view_ready
        ])
        .run(tauri::generate_context!())
        .expect("Could not start Artemis desktop client");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compact_status_is_off_unless_the_launcher_asks_for_it() {
        assert!(compact_requested(Some("1".into())));
        for value in [None, Some(""), Some("0"), Some("true"), Some(" 1")] {
            assert!(!compact_requested(value.map(String::from)));
        }
        let config = CoreConfig::from_values(
            Some("http://127.0.0.1:8123".into()),
            Some("test-only".into()),
        )
        .unwrap_or_else(|_| panic!("valid fixture rejected"));
        assert!(!config.compact);
        assert!(config.with_compact(true).compact);
    }

    #[test]
    fn only_the_main_window_can_receive_the_launch_token() {
        let state = LaunchState(CoreConfig::from_values(
            Some("http://127.0.0.1:8123".into()),
            Some("test-only".into()),
        ));
        assert!(config_for_window("main", &state).is_ok());
        assert!(config_for_window("companion", &state).is_err());
        assert!(config_for_window("untrusted", &state).is_err());
    }

    #[test]
    fn accepts_only_loopback_root_and_a_nonempty_header_safe_token() {
        let config = CoreConfig::from_values(
            Some("http://127.0.0.1:8123/".into()),
            Some("test-only_token".into()),
        )
        .unwrap_or_else(|_| panic!("valid fixture rejected"));
        assert_eq!(config.core_url, "http://127.0.0.1:8123");
        for address in [
            "https://127.0.0.1:8123",
            "http://example.com",
            "http://127.0.0.1:8123/path",
            "http://user:pass@127.0.0.1:8123",
            "http://127.0.0.1:8123/?token=secret",
            "http://127.0.0.1:8123/#secret",
        ] {
            assert!(
                CoreConfig::from_values(Some(address.into()), Some("test-only".into())).is_err()
            );
        }
        for token in ["", " ", "bad\r\nheader", "trailing "] {
            assert!(CoreConfig::from_values(
                Some("http://127.0.0.1:8123".into()),
                Some(token.into())
            )
            .is_err());
        }
    }

    #[test]
    fn missing_values_return_only_static_recovery_text() {
        for (address, token) in [
            (None, None),
            (Some("http://127.0.0.1:8123".into()), None),
            (None, Some("never-print-this".into())),
        ] {
            let error = CoreConfig::from_values(address, token)
                .err()
                .expect("missing value accepted");
            assert_eq!(error, LAUNCH_ERROR);
            assert!(!error.contains("never-print-this"));
        }
    }
}
