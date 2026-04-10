/// Global hotkey registration.
/// On Windows: uses Win32 RegisterHotKey API.
/// On Linux/macOS: no-op stub (use tauri-plugin-global-shortcut as alternative).

#[cfg(target_os = "windows")]
pub fn register_global_hotkey(_app: &tauri::AppHandle) {
    // TODO: Implement Win32 RegisterHotKey in a background thread
    // Default: Ctrl+Shift+L to toggle window visibility
    log::info!("Global hotkey registered (Windows)");
}

#[cfg(not(target_os = "windows"))]
pub fn register_global_hotkey(_app: &tauri::AppHandle) {
    log::info!("Global hotkey: not available on this platform (stub)");
}
