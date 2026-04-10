mod tray;
mod hotkey;
mod autostart;
mod sensing;
mod commands;

use std::sync::Arc;
use parking_lot::Mutex;
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec!["--minimized"]),
        ))
        .plugin(tauri_plugin_notification::init())
        .invoke_handler(tauri::generate_handler![
            commands::get_app_usage_today,
            commands::get_session_events,
            commands::get_silence_state,
        ])
        .setup(|app| {
            // Initialize sensing database
            let db = sensing::db::SensingDb::open()
                .map_err(|e| format!("Failed to open sensing DB: {e}"))?;
            let db = Arc::new(Mutex::new(db));

            // Record boot event
            sensing::session_events::record_boot(&db);

            // Manage DB state for Tauri commands
            app.manage(db.clone());

            // Start sensing engine in background
            let db_clone = db.clone();
            let server_url = "http://127.0.0.1:8765".to_string(); // TODO: read from config
            tauri::async_runtime::spawn(async move {
                // File watcher
                let db_fw = db_clone.clone();
                tokio::spawn(async move {
                    sensing::file_watcher::run(db_fw).await;
                });

                // Window monitor
                let db_wm = db_clone.clone();
                tokio::spawn(async move {
                    sensing::window_monitor::run(db_wm).await;
                });

                // Aggregator
                let db_ag = db_clone;
                tokio::spawn(async move {
                    sensing::aggregator::run(db_ag, server_url).await;
                });
            });

            // System tray
            tray::setup_tray(app)?;

            // Global hotkey
            hotkey::register_global_hotkey(app.handle());

            // Handle --minimized (boot to tray)
            if autostart::is_minimized_start() {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
            }

            // Intercept window close → hide to tray
            let app_handle = app.handle().clone();
            app.get_webview_window("main")
                .unwrap()
                .on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        if let Some(window) = app_handle.get_webview_window("main") {
                            let _ = window.hide();
                        }
                    }
                });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
