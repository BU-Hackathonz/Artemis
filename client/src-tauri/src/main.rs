#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod caret;

use serde::Serialize;
use tauri::Manager;

#[derive(Clone, Serialize)]
struct CoreConfig {
    core_url: String,
    token: String,
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
        })
    }

    fn from_env() -> Result<Self, String> {
        // Read exactly once at process start. Never log or derive Debug for these values.
        Self::from_values(
            std::env::var("ARTEMIS_CORE_URL").ok(),
            std::env::var("ARTEMIS_TOKEN").ok(),
        )
    }
}

struct LaunchState(Result<CoreConfig, String>);

#[tauri::command]
fn get_core_config(state: tauri::State<'_, LaunchState>) -> Result<CoreConfig, String> {
    state.inner().0.clone()
}

/// Where the text caret is (physical screen pixels), or None. For the flow window only: the label
/// comes from the window that invoked the command, never from an argument, so a page cannot claim
/// to be the flow window. It returns two numbers and nothing else.
#[tauri::command]
fn get_caret_position(window: tauri::WebviewWindow) -> Result<Option<(i32, i32)>, String> {
    if window.label() != "flow" {
        return Err("Not available to this window.".into());
    }
    Ok(caret::position())
}

fn main() {
    let state = LaunchState(CoreConfig::from_env());
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(state)
        .invoke_handler(tauri::generate_handler![get_core_config, get_caret_position])
        // The desktop agent window must not keep Artemis running once the main window is gone.
        .on_window_event(|window, event| {
            if window.label() == "main" {
                if let tauri::WindowEvent::Destroyed = event {
                    window.app_handle().exit(0);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("Could not start Artemis desktop client");
}

#[cfg(test)]
mod tests {
    use super::*;

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
