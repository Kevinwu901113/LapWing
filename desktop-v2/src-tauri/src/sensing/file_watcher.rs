/// File system monitoring using the `notify` crate (cross-platform).

use std::sync::Arc;
use parking_lot::Mutex;
use notify::{Watcher, RecursiveMode, Event, EventKind};
use super::db::SensingDb;

const IGNORED_EXTENSIONS: &[&str] = &[".tmp", ".swp", ".pyc", ".pyo"];
const IGNORED_NAMES: &[&str] = &["thumbs.db", "desktop.ini", ".ds_store"];
const IGNORED_DIRS: &[&str] = &[".git", "node_modules", "__pycache__", ".venv"];

fn should_ignore(path: &std::path::Path) -> bool {
    let name = path.file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("")
        .to_lowercase();

    if IGNORED_NAMES.contains(&name.as_str()) {
        return true;
    }
    if name.starts_with("~$") || name.starts_with('.') {
        return true;
    }
    if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
        let ext_dot = format!(".{}", ext.to_lowercase());
        if IGNORED_EXTENSIONS.contains(&ext_dot.as_str()) {
            return true;
        }
    }
    for component in path.components() {
        if let std::path::Component::Normal(c) = component {
            let s = c.to_str().unwrap_or("");
            if IGNORED_DIRS.contains(&s) {
                return true;
            }
        }
    }
    false
}

pub async fn run(db: Arc<Mutex<SensingDb>>) {
    // Default watch directories
    let home = dirs::home_dir().unwrap_or_default();
    let watch_dirs = vec![
        home.join("Desktop"),
        home.join("Documents"),
        home.join("Downloads"),
    ];

    let (tx, rx) = std::sync::mpsc::channel::<notify::Result<Event>>();

    let mut watcher = match notify::recommended_watcher(tx) {
        Ok(w) => w,
        Err(e) => {
            log::error!("Failed to create file watcher: {e}");
            return;
        }
    };

    for dir in &watch_dirs {
        if dir.exists() {
            if let Err(e) = watcher.watch(dir, RecursiveMode::Recursive) {
                log::warn!("Failed to watch {}: {e}", dir.display());
            }
        }
    }

    log::info!("File watcher started for {} directories", watch_dirs.len());

    // Process events in a blocking thread
    tokio::task::spawn_blocking(move || {
        for result in rx {
            if let Ok(event) = result {
                for path in &event.paths {
                    if should_ignore(path) {
                        continue;
                    }
                    let event_type = match event.kind {
                        EventKind::Create(_) => "create",
                        EventKind::Modify(_) => "modify",
                        EventKind::Remove(_) => "delete",
                        _ => continue,
                    };
                    let file_name = path.file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or("unknown");

                    if let Some(db) = db.try_lock() {
                        let _ = db.insert_file_event(
                            event_type,
                            &path.to_string_lossy(),
                            file_name,
                        );
                    }
                }
            }
        }
    })
    .await
    .ok();
}
