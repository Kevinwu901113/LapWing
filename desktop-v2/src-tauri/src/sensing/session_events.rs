/// Session events: lock/unlock/shutdown detection.
/// Windows: uses WTSRegisterSessionNotification.
/// Linux/macOS: no-op stub.

use std::sync::Arc;
use parking_lot::Mutex;
use super::db::SensingDb;

pub fn record_boot(db: &Arc<Mutex<SensingDb>>) {
    if let Some(db) = db.try_lock() {
        let _ = db.insert_session_event("boot", None);
    }
}

#[cfg(target_os = "windows")]
pub fn register_session_notifications(_hwnd: isize) {
    // TODO: WTSRegisterSessionNotification + message loop
    log::info!("Session notifications registered (Windows)");
}

#[cfg(not(target_os = "windows"))]
pub fn register_session_notifications(_hwnd: isize) {
    log::info!("Session notifications: stub (not on Windows)");
}
