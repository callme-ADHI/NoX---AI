fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&[
                "get_session_token",
                "start_daemon",
                "open_in_browser",
                "check_daemon_health",
            ]),
        ),
    )
    .unwrap();
}
