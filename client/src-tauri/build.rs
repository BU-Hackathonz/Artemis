fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(&["get_core_config", "get_caret_position"])),
    )
    .expect("Could not build Artemis desktop resources");
}
