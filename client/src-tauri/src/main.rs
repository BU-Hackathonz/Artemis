#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use tauri::Manager;
mod companion;

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

fn main() {
    let state = LaunchState(CoreConfig::from_env());
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(state)
        .manage(companion::CompanionState::default())
        .setup(|app| {
            let compact_shown = companion::place(app.handle()).is_ok()
                && app
                    .get_webview_window("companion")
                    .is_some_and(|status| status.show().is_ok());
            if !compact_shown {
                if let Some(main) = app.get_webview_window("main") {
                    let _ = main.show();
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
            companion::enter_compact
        ])
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
