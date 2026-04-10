use crate::sensing::db::{AppUsageRecord, SessionEvent};
use std::sync::Arc;
use parking_lot::Mutex;

pub type DbState = Arc<Mutex<crate::sensing::db::SensingDb>>;

#[tauri::command]
pub async fn get_app_usage_today(
    db: tauri::State<'_, DbState>,
) -> Result<Vec<AppUsageRecord>, String> {
    let db = db.lock();
    db.get_app_usage_today().map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn get_session_events(
    limit: Option<u32>,
    db: tauri::State<'_, DbState>,
) -> Result<Vec<SessionEvent>, String> {
    let db = db.lock();
    db.get_session_events(limit.unwrap_or(50)).map_err(|e| e.to_string())
}

#[derive(serde::Serialize)]
pub struct SilenceState {
    pub active: bool,
    pub game_name: Option<String>,
    pub started_at: Option<String>,
}

#[tauri::command]
pub async fn get_silence_state() -> Result<SilenceState, String> {
    let is_gaming = crate::sensing::process_detector::is_game_running();
    let game = crate::sensing::process_detector::get_current_game();
    Ok(SilenceState {
        active: is_gaming,
        game_name: game,
        started_at: None,
    })
}
