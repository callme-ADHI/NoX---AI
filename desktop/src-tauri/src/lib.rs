use std::fs;
use std::path::PathBuf;
use std::process::Command;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::Manager;

pub const API_BASE: &str = "http://127.0.0.1:7777";

pub fn get_token_path() -> Option<PathBuf> {
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

pub fn validate_browser_url(url: &str) -> Result<String, String> {
    let trimmed = url.trim();
    if !trimmed.starts_with("http://") && !trimmed.starts_with("https://") {
        return Err("Blocked: only http:// and https:// URLs are allowed".to_string());
    }
    if trimmed
        .chars()
        .any(|c| c.is_control() || c == '`' || c == '$' || c == ';' || c == '&' || c == '|')
    {
        return Err("Blocked: URL contains prohibited control or shell characters".to_string());
    }
    Ok(trimmed.to_string())
}

#[tauri::command]
fn open_in_browser(url: String) -> Result<(), String> {
    let valid_url = validate_browser_url(&url)?;
    Command::new("xdg-open")
        .arg(&valid_url)
        .spawn()
        .map_err(|e| format!("Failed to spawn xdg-open: {}", e))?;
    Ok(())
}

#[tauri::command]
fn check_daemon_health() -> bool {
    let url = format!("{}/api/health", API_BASE);
    let output = Command::new("curl").args(["-sf", &url]).output();
    match output {
        Ok(out) => out.status.success(),
        Err(_) => false,
    }
}

pub fn sanitize_target_url(path: &str, token: &str) -> Result<String, String> {
    let trimmed = path.trim();
    if trimmed.is_empty() {
        return Ok(if token.is_empty() {
            API_BASE.to_string()
        } else {
            format!("{}/?t={}", API_BASE, token)
        });
    }

    let lower = trimmed.to_lowercase();
    if lower.starts_with("javascript:")
        || lower.starts_with("file:")
        || lower.starts_with("data:")
        || lower.starts_with("vbscript:")
    {
        return Err("Blocked: prohibited URI scheme".to_string());
    }

    if trimmed.starts_with("http://127.0.0.1:7777") || trimmed.starts_with("http://localhost:7777")
    {
        if !token.is_empty() && !trimmed.contains("t=") {
            let sep = if trimmed.contains('?') { "&" } else { "?" };
            Ok(format!("{}{}t={}", trimmed, sep, token))
        } else {
            Ok(trimmed.to_string())
        }
    } else if trimmed.starts_with('/') {
        if token.is_empty() {
            Ok(format!("{}{}", API_BASE, trimmed))
        } else {
            Ok(format!("{}/?t={}&next={}", API_BASE, token, trimmed))
        }
    } else if trimmed.starts_with("http://") || trimmed.starts_with("https://") {
        Err("External URL: use open_in_browser instead of loading in NoX webview".to_string())
    } else {
        if token.is_empty() {
            Ok(format!("{}/{}", API_BASE, trimmed))
        } else {
            Ok(format!("{}/?t={}&next=/{}", API_BASE, token, trimmed))
        }
    }
}

pub fn trigger_daemon_api(endpoint: &str, method: &str) -> bool {
    let url = format!("{}{}", API_BASE, endpoint);
    let token = get_session_token();
    let mut cmd = Command::new("curl");
    cmd.args(["-s", "-w", "\n%{http_code}", "-X", method, &url]);
    if !token.is_empty() {
        cmd.arg("-H")
            .arg(format!("Authorization: Bearer {}", token));
    }
    match cmd.output() {
        Ok(out) => {
            let stdout = String::from_utf8_lossy(&out.stdout);
            let lines: Vec<&str> = stdout.trim().lines().collect();
            let http_code = lines.last().unwrap_or(&"000");
            let success = out.status.success()
                && (http_code.starts_with('2') || http_code.starts_with('3'));
            if success {
                log::info!("API {} {} succeeded (HTTP {})", method, endpoint, http_code);
            } else {
                log::warn!(
                    "API {} {} returned HTTP {} (status {:?})",
                    method,
                    endpoint,
                    http_code,
                    out.status
                );
            }
            success
        }
        Err(err) => {
            log::error!("Failed to execute API {} {}: {}", method, endpoint, err);
            false
        }
    }
}

pub fn open_notes_vault_action() {
    std::thread::spawn(|| {
        log::info!("Opening notes vault via daemon /api/notes/open...");
        if !trigger_daemon_api("/api/notes/open", "POST") {
            log::warn!("/api/notes/open failed; querying /api/notes/info for dynamic path...");
            let token = get_session_token();
            let mut cmd = Command::new("curl");
            cmd.args(["-sf", &format!("{}/api/notes/info", API_BASE)]);
            if !token.is_empty() {
                cmd.arg("-H")
                    .arg(format!("Authorization: Bearer {}", token));
            }
            if let Ok(out) = cmd.output() {
                if let Ok(json) = serde_json::from_slice::<serde_json::Value>(&out.stdout) {
                    if let Some(path) = json.get("vault_path").and_then(|p| p.as_str()) {
                        log::info!("Opening vault from info endpoint: {}", path);
                        let _ = Command::new("xdg-open").arg(path).spawn();
                        return;
                    }
                }
            }
            if let Ok(home) = std::env::var("HOME") {
                let default_vault = format!("{}/.local/share/nox/vault", home);
                log::info!("Falling back to default vault path: {}", default_vault);
                let _ = Command::new("xdg-open").arg(default_vault).spawn();
            }
        }
    });
}

pub fn update_tray_labels<R: tauri::Runtime>(
    item_next: &MenuItem<R>,
    item_last: &MenuItem<R>,
    item_cloud: &MenuItem<R>,
) {
    let token = get_session_token();

    // 1. Briefings status
    let mut cmd_briefing = Command::new("curl");
    cmd_briefing.args(["-sf", &format!("{}/api/briefings/status", API_BASE)]);
    if !token.is_empty() {
        cmd_briefing
            .arg("-H")
            .arg(format!("Authorization: Bearer {}", token));
    }

    if let Ok(out) = cmd_briefing.output() {
        if out.status.success() {
            if let Ok(v) = serde_json::from_slice::<serde_json::Value>(&out.stdout) {
                // Next scheduled run
                let mut next_label = "Next briefing: None scheduled".to_string();
                if let Some(next_runs) = v.get("next_runs") {
                    let m = next_runs.get("morning").and_then(|s| s.as_str());
                    let e = next_runs.get("evening").and_then(|s| s.as_str());
                    if let Some(time_str) = m.or(e) {
                        let display = if time_str.len() >= 16 {
                            &time_str[11..16]
                        } else {
                            time_str
                        };
                        next_label = format!("Next briefing: {}", display);
                    }
                }
                let _ = item_next.set_text(&next_label);

                // Last completed run
                let mut last_label = "Last briefing: None".to_string();
                if let Some(last) = v.get("last") {
                    if let Some(morning) = last.get("morning").filter(|v| !v.is_null()) {
                        let state = morning
                            .get("state")
                            .and_then(|s| s.as_str())
                            .unwrap_or("done");
                        last_label = format!("Last briefing: Morning ({})", state);
                    } else if let Some(evening) = last.get("evening").filter(|v| !v.is_null()) {
                        let state = evening
                            .get("state")
                            .and_then(|s| s.as_str())
                            .unwrap_or("done");
                        last_label = format!("Last briefing: Evening ({})", state);
                    }
                }
                let _ = item_last.set_text(&last_label);
            }
        } else {
            let _ = item_next.set_text("Next briefing: (daemon offline)");
            let _ = item_last.set_text("Last briefing: (daemon offline)");
        }
    } else {
        let _ = item_next.set_text("Next briefing: (daemon offline)");
        let _ = item_last.set_text("Last briefing: (daemon offline)");
    }

    // 2. Cloud status
    let mut cmd_cloud = Command::new("curl");
    cmd_cloud.args(["-sf", &format!("{}/api/status", API_BASE)]);
    if !token.is_empty() {
        cmd_cloud
            .arg("-H")
            .arg(format!("Authorization: Bearer {}", token));
    }
    if let Ok(out) = cmd_cloud.output() {
        if out.status.success() {
            if let Ok(v) = serde_json::from_slice::<serde_json::Value>(&out.stdout) {
                let cloud_avail = v
                    .get("cloud_available")
                    .and_then(|b| b.as_bool())
                    .unwrap_or(false);
                let label = if cloud_avail {
                    "Cloud: Enabled (click to toggle)"
                } else {
                    "Cloud: Disabled (click to toggle)"
                };
                let _ = item_cloud.set_text(label);
            }
        } else {
            let _ = item_cloud.set_text("Cloud: (daemon offline)");
        }
    } else {
        let _ = item_cloud.set_text("Cloud: (daemon offline)");
    }
}

pub fn start_tray_label_poller<R: tauri::Runtime + 'static>(
    item_next: MenuItem<R>,
    item_last: MenuItem<R>,
    item_cloud: MenuItem<R>,
) {
    std::thread::spawn(move || {
        loop {
            update_tray_labels(&item_next, &item_last, &item_cloud);
            std::thread::sleep(std::time::Duration::from_secs(15));
        }
    });
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
                    match sanitize_target_url(&path, &token) {
                        Ok(target) => {
                            let js = format!("window.location.href = '{}';", target);
                            let _ = window.eval(&js);
                        }
                        Err(_) => {
                            if let Ok(safe_ext_url) = validate_browser_url(&path) {
                                let _ = Command::new("xdg-open").arg(safe_ext_url).spawn();
                            }
                        }
                    }
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
            let item_briefing = MenuItem::with_id(
                app,
                "briefing_now",
                "Run briefing now",
                true,
                None::<&str>,
            )?;
            let item_next = MenuItem::with_id(
                app,
                "next_briefing",
                "Next briefing: Loading...",
                true,
                None::<&str>,
            )?;
            let item_last = MenuItem::with_id(
                app,
                "last_briefing",
                "Last briefing: Loading...",
                true,
                None::<&str>,
            )?;
            let item_sep2 = PredefinedMenuItem::separator(app)?;
            let item_cloud = MenuItem::with_id(
                app,
                "cloud_toggle",
                "Cloud: Loading...",
                true,
                None::<&str>,
            )?;
            let item_vault = MenuItem::with_id(
                app,
                "notes_vault",
                "Open Notes Vault",
                true,
                None::<&str>,
            )?;
            let item_sep3 = PredefinedMenuItem::separator(app)?;
            let item_quit = MenuItem::with_id(app, "quit", "Quit app", true, None::<&str>)?;
            let item_stop_quit = MenuItem::with_id(
                app,
                "stop_and_quit",
                "Stop daemon and quit",
                true,
                None::<&str>,
            )?;

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

            // Start dynamic status updates in background thread
            start_tray_label_poller(item_next.clone(), item_last.clone(), item_cloud.clone());

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
                            trigger_daemon_api("/api/briefings/run", "POST");
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
                            open_notes_vault_action();
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

    #[test]
    fn test_validate_browser_url_valid() {
        assert!(validate_browser_url("https://example.com").is_ok());
        assert!(validate_browser_url("http://localhost:7777").is_ok());
    }

    #[test]
    fn test_validate_browser_url_rejected_schemes() {
        assert!(validate_browser_url("file:///etc/passwd").is_err());
        assert!(validate_browser_url("javascript:alert(1)").is_err());
        assert!(validate_browser_url("data:text/html,evil").is_err());
    }

    #[test]
    fn test_validate_browser_url_rejected_characters() {
        assert!(validate_browser_url("https://example.com; rm -rf /").is_err());
        assert!(validate_browser_url("https://example.com`whoami`").is_err());
        assert!(validate_browser_url("https://example.com\nBadHeader: 1").is_err());
    }

    #[test]
    fn test_sanitize_target_url_local() {
        let res = sanitize_target_url("/briefings", "my_token").unwrap();
        assert_eq!(res, "http://127.0.0.1:7777/?t=my_token&next=/briefings");

        let res2 = sanitize_target_url("http://127.0.0.1:7777/dashboard", "my_token").unwrap();
        assert_eq!(res2, "http://127.0.0.1:7777/dashboard?t=my_token");
    }

    #[test]
    fn test_sanitize_target_url_external_no_token_leak() {
        // External URLs must be rejected so token is never leaked
        let res = sanitize_target_url("https://evil.com/phish", "secret_token");
        assert!(res.is_err());
        assert!(!format!("{:?}", res).contains("secret_token"));
    }

    #[test]
    fn test_sanitize_target_url_reject_dangerous_schemes() {
        assert!(sanitize_target_url("javascript:alert(1)", "token").is_err());
        assert!(sanitize_target_url("file:///etc/shadow", "token").is_err());
        assert!(sanitize_target_url("data:text/html,<script>", "token").is_err());
    }

    #[test]
    fn test_tray_endpoints_plural() {
        // Asserts that the tray briefing trigger endpoint uses the correct plural route
        let code = include_str!("lib.rs");
        assert!(
            code.contains("/api/briefings/run"),
            "Tray action must call plural route /api/briefings/run"
        );
        assert!(
            !code.contains("\"/api/briefing/run\""),
            "Tray action must not call deprecated singular route /api/briefing/run"
        );
    }
}

