pub mod db;
pub mod window_monitor;
pub mod process_detector;
pub mod session_events;
pub mod clipboard;
pub mod file_watcher;
pub mod aggregator;

use std::sync::Arc;
use parking_lot::Mutex;
use tokio::task::JoinHandle;

pub struct SensingEngine {
    db: Arc<Mutex<db::SensingDb>>,
    _handles: Vec<JoinHandle<()>>,
}

impl SensingEngine {
    pub async fn start(server_url: String) -> Result<Self, String> {
        let db = db::SensingDb::open()
            .map_err(|e| format!("Failed to open sensing DB: {e}"))?;
        let db = Arc::new(Mutex::new(db));

        let mut handles = Vec::new();

        // File watcher (cross-platform)
        let db_clone = db.clone();
        handles.push(tokio::spawn(async move {
            file_watcher::run(db_clone).await;
        }));

        // Window monitor (Windows-only, stub on Linux)
        let db_clone = db.clone();
        handles.push(tokio::spawn(async move {
            window_monitor::run(db_clone).await;
        }));

        // Aggregator (cross-platform)
        let db_clone = db.clone();
        handles.push(tokio::spawn(async move {
            aggregator::run(db_clone, server_url).await;
        }));

        Ok(Self {
            db,
            _handles: handles,
        })
    }

    pub fn get_db(&self) -> Arc<Mutex<db::SensingDb>> {
        self.db.clone()
    }
}
