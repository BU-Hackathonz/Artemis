fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "get_core_config",
            "get_companion_status",
            "update_companion_status",
            "show_main",
            "enter_compact",
            "reveal_approval",
            "register_controls_handler",
            "show_settings",
            "set_controls_view",
            "controls_view_ready",
            "get_flow_state",
            "set_flow_visible",
            "get_caret_position",
        ]),
    ))
    .expect("Could not build Artemis desktop resources");
}
