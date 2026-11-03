use std::fs;
use std::path::PathBuf;
use std::process::Command;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::Manager;

const API_BASE: &str = "http://127.0.0.1:7777";

fn get_token_path() -> Option<PathBuf> {
    if let Ok(home) = std::env::var("HOME") {
        Some(PathBuf::from(home).join(".local/share/nox/session_token"))
    } else {
        None
    }
}

#[tauri::command]
fn get_session_token() -> String {
    if let Some(path) = get_token_path() {
        if path.exists() {
            if let Ok(token) = fs::read_to_string(path) {
                return token.trim().to_string();
            }
        }
    }
    String::new()
}

#[tauri::command]
fn start_daemon() {
    let _ = Command::new("systemctl")
        .args(["--user", "start", "noxd"])
        .spawn();
}

#[tauri::command]
fn open_in_browser(url: String) {
    let _ = Command::new("xdg-open").arg(url).spawn();
}

#[tauri::command]
fn check_daemon_health() -> bool {
    let url = format!("{}/api/health", API_BASE);
    let output = Command::new("curl")
        .args(["-sf", &url])
        .output();
    match output {
        Ok(out) => out.status.success(),
        Err(_) => false,
    }
}

fn trigger_daemon_api(endpoint: &str, method: &str) {
    let url = format!("{}{}", API_BASE, endpoint);
    let token = get_session_token();
    let mut cmd = Command::new("curl");
    cmd.arg("-sf").arg("-X").arg(method).arg(&url);
    if !token.is_empty() {
        cmd.arg("-H").arg(format!("Authorization: Bearer {}", token));
    }
    let _ = cmd.spawn();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default();

    // Single-instance enforcement
    builder = builder.plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
        if let Some(window) = app.get_webview_window("main") {
            let mut hidden = false;
            let mut target_open: Option<String> = None;
            let mut iter = args.iter();
            while let Some(arg) = iter.next() {
                if arg == "--hidden" {
                    hidden = true;
                } else if arg == "--open" {
                    if let Some(path) = iter.next() {
                        target_open = Some(path.clone());
                    }
                }
            }

            if !hidden {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
                if let Some(path) = target_open {
                    let token = get_session_token();
                    let target = if token.is_empty() {
                        if path.starts_with("http") {
                            path
                        } else {
                            format!("{}{}", API_BASE, path)
                        }
                    } else if path.starts_with("http") {
                        path
                    } else {
                        let clean_path = if path.starts_with('/') { path } else { format!("/{}", path) };
                        format!("{}/?t={}&next={}", API_BASE, token, clean_path)
                    };
                    let js = format!("window.location.href = '{}';", target);
                    let _ = window.eval(&js);
                }
            }
        }
    }));

    builder
        .plugin(
            tauri_plugin_log::Builder::default()
                .level(log::LevelFilter::Info)
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            get_session_token,
            start_daemon,
            open_in_browser,
            check_daemon_health
        ])
        .setup(|app| {
            // Build Tray Menu
            let item_open = MenuItem::with_id(app, "open", "Open NoX", true, None::<&str>)?;
            let item_sep1 = PredefinedMenuItem::separator(app)?;
            let item_briefing = MenuItem::with_id(app, "briefing_now", "Run briefing now", true, None::<&str>)?;
            let item_next = MenuItem::with_id(app, "next_briefing", "Next briefing", true, None::<&str>)?;
            let item_last = MenuItem::with_id(app, "last_briefing", "Last briefing", true, None::<&str>)?;
            let item_sep2 = PredefinedMenuItem::separator(app)?;
            let item_cloud = MenuItem::with_id(app, "cloud_toggle", "Cloud toggle", true, None::<&str>)?;
            let item_vault = MenuItem::with_id(app, "notes_vault", "Open Notes Vault", true, None::<&str>)?;
            let item_sep3 = PredefinedMenuItem::separator(app)?;
            let item_quit = MenuItem::with_id(app, "quit", "Quit app", true, None::<&str>)?;
            let item_stop_quit = MenuItem::with_id(app, "stop_and_quit", "Stop daemon and quit", true, None::<&str>)?;

            let tray_menu = Menu::with_items(
                app,
                &[
                    &item_open,
                    &item_sep1,
                    &item_briefing,
                    &item_next,
                    &item_last,
                    &item_sep2,
                    &item_cloud,
                    &item_vault,
                    &item_sep3,
                    &item_quit,
                    &item_stop_quit,
                ],
            )?;

            let icon = app.default_window_icon().cloned().expect("icon must exist");
            let _tray = TrayIconBuilder::new()
                .icon(icon)
                .menu(&tray_menu)
                .tooltip("NoX — Personal AI Agent")
                .on_menu_event(|app, event| {
                    match event.id().as_ref() {
                        "open" => {
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.show();
                                let _ = w.unminimize();
                                let _ = w.set_focus();
                            }
                        }
                        "briefing_now" => {
                            trigger_daemon_api("/api/briefing/run", "POST");
                        }
                        "next_briefing" => {
                            let _ = Command::new("notify-send")
                                .args(["NoX Briefing", "Next briefing schedule: 04:00 (morning) and 18:00 (evening)"])
                                .spawn();
                        }
                        "last_briefing" => {
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.show();
                                let _ = w.set_focus();
                                let token = get_session_token();
                                let target = if token.is_empty() {
                                    format!("{}/briefings", API_BASE)
                                } else {
                                    format!("{}/?t={}&next=/briefings", API_BASE, token)
                                };
                                let js = format!("window.location.href = '{}';", target);
                                let _ = w.eval(&js);
                            }
                        }
                        "cloud_toggle" => {
                            trigger_daemon_api("/api/cloud/toggle", "POST");
                        }
                        "notes_vault" => {
                            if let Ok(home) = std::env::var("HOME") {
                                let vault_dir = format!("{}/.local/share/nox/vault", home);
                                let _ = Command::new("xdg-open").arg(vault_dir).spawn();
                            }
                        }
                        "quit" => {
                            app.exit(0);
                        }
                        "stop_and_quit" => {
                            let _ = Command::new("systemctl")
                                .args(["--user", "stop", "noxd"])
                                .spawn();
                            app.exit(0);
                        }
                        _ => {}
                    }
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(window) = app.get_webview_window("main") {
                            if window.is_visible().unwrap_or(false) {
                                let _ = window.hide();
                            } else {
                                let _ = window.show();
                                let _ = window.set_focus();
                            }
                        }
                    }
                })
                .build(app)?;

            // Window close behavior: hide to tray
            if let Some(window) = app.get_webview_window("main") {
                let token = get_session_token();
                if !token.is_empty() {
                    let js = format!("window.__NOX_TOKEN__ = '{}';", token);
                    let _ = window.eval(&js);
                }

                let window_clone = window.clone();
                window.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = window_clone.hide();
                    }
                });

                // Check CLI arguments for --hidden
                let args: Vec<String> = std::env::args().collect();
                if args.iter().any(|a| a == "--hidden") {
                    let _ = window.hide();
                }
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_token_path_resolution() {
        let path = get_token_path();
        assert!(path.is_some());
        let p = path.unwrap();
        assert!(p.ends_with(".local/share/nox/session_token"));
    }

    #[test]
    fn test_session_token_retrieval() {
        let token = get_session_token();
        assert!(!token.contains('\n'));
        assert!(!token.contains('\r'));
    }

    #[test]
    fn test_url_path_formatting() {
        let relative_path = "/briefings";
        let target = if relative_path.starts_with("http") {
            relative_path.to_string()
        } else {
            format!("{}{}", API_BASE, relative_path)
        };
        assert_eq!(target, "http://127.0.0.1:7777/briefings");
    }
}
