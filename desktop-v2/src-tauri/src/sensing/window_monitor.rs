use std::sync::Arc;
use parking_lot::Mutex;
use super::db::SensingDb;

/// Window monitor: polls the foreground window every second.
/// Windows: uses GetForegroundWindow + GetWindowText APIs.
/// Linux/macOS: no-op stub.

#[cfg(target_os = "windows")]
pub async fn run(db: Arc<Mutex<SensingDb>>) {
    // TODO: Implement Windows foreground window polling
    // - GetForegroundWindow → HWND
    // - GetWindowTextW → window title
    // - GetWindowThreadProcessId → PID → process name
    // - Compare with previous, record changes
    loop {
        tokio::time::sleep(std::time::Duration::from_secs(1)).await;
    }
}

#[cfg(not(target_os = "windows"))]
pub async fn run(_db: Arc<Mutex<SensingDb>>) {
    log::info!("Window monitor: stub (not on Windows)");
    // Sleep forever — no data on non-Windows platforms
    loop {
        tokio::time::sleep(std::time::Duration::from_secs(3600)).await;
    }
}
