fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "get_core_config",
            "get_companion_status",
            "update_companion_status",
            "show_main",
            "enter_compact",
            "reveal_approval",
        ]),
    ))
    .expect("Could not build Artemis desktop resources");
}
