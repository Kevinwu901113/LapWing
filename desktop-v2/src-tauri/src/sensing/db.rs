use rusqlite::{Connection, Result, params};
use serde::Serialize;

pub struct SensingDb {
    conn: Connection,
}

#[derive(Serialize, Clone)]
pub struct AppUsageRecord {
    pub process_name: String,
    pub app_display_name: String,
    pub total_seconds: i64,
    pub category: String,
}

#[derive(Serialize, Clone)]
pub struct WindowEvent {
    pub timestamp: String,
    pub process_name: String,
    pub window_title: String,
    pub duration_seconds: i64,
}

#[derive(Serialize, Clone)]
pub struct SessionEvent {
    pub timestamp: String,
    pub event_type: String,
    pub detail: Option<String>,
}

impl SensingDb {
    pub fn open() -> Result<Self> {
        let data_dir = dirs::data_dir()
            .unwrap_or_else(|| std::path::PathBuf::from("."))
            .join("Lapwing");
        std::fs::create_dir_all(&data_dir).ok();

        let conn = Connection::open(data_dir.join("sensing.db"))?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS window_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                process_name TEXT NOT NULL,
                window_title TEXT NOT NULL,
                duration_seconds INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS app_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                process_name TEXT NOT NULL,
                app_display_name TEXT,
                total_seconds INTEGER DEFAULT 0,
                category TEXT,
                UNIQUE(date, process_name)
            );
            CREATE TABLE IF NOT EXISTS session_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                detail TEXT
            );
            CREATE TABLE IF NOT EXISTS clipboard_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                content_type TEXT NOT NULL,
                content_preview TEXT,
                char_count INTEGER
            );
            CREATE TABLE IF NOT EXISTS file_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                path TEXT NOT NULL,
                file_name TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_window_events_ts ON window_events(timestamp);
            CREATE INDEX IF NOT EXISTS idx_app_usage_date ON app_usage(date);
            CREATE INDEX IF NOT EXISTS idx_session_events_ts ON session_events(timestamp);"
        )?;
        Ok(Self { conn })
    }

    pub fn insert_window_event(&self, process_name: &str, window_title: &str) -> Result<()> {
        let ts = chrono::Utc::now().to_rfc3339();
        self.conn.execute(
            "INSERT INTO window_events (timestamp, process_name, window_title) VALUES (?1, ?2, ?3)",
            params![ts, process_name, window_title],
        )?;
        Ok(())
    }

    pub fn insert_session_event(&self, event_type: &str, detail: Option<&str>) -> Result<()> {
        let ts = chrono::Utc::now().to_rfc3339();
        self.conn.execute(
            "INSERT INTO session_events (timestamp, event_type, detail) VALUES (?1, ?2, ?3)",
            params![ts, event_type, detail],
        )?;
        Ok(())
    }

    pub fn insert_file_event(&self, event_type: &str, path: &str, file_name: &str) -> Result<()> {
        let ts = chrono::Utc::now().to_rfc3339();
        self.conn.execute(
            "INSERT INTO file_events (timestamp, event_type, path, file_name) VALUES (?1, ?2, ?3, ?4)",
            params![ts, event_type, path, file_name],
        )?;
        Ok(())
    }

    pub fn get_app_usage_today(&self) -> Result<Vec<AppUsageRecord>> {
        let today = chrono::Local::now().format("%Y-%m-%d").to_string();
        let mut stmt = self.conn.prepare(
            "SELECT process_name, app_display_name, total_seconds, category
             FROM app_usage WHERE date = ?1 ORDER BY total_seconds DESC"
        )?;
        let rows = stmt.query_map(params![today], |row| {
            Ok(AppUsageRecord {
                process_name: row.get(0)?,
                app_display_name: row.get::<_, Option<String>>(1)?.unwrap_or_default(),
                total_seconds: row.get(2)?,
                category: row.get::<_, Option<String>>(3)?.unwrap_or_default(),
            })
        })?;
        rows.collect()
    }

    pub fn get_session_events(&self, limit: u32) -> Result<Vec<SessionEvent>> {
        let mut stmt = self.conn.prepare(
            "SELECT timestamp, event_type, detail FROM session_events
             ORDER BY id DESC LIMIT ?1"
        )?;
        let rows = stmt.query_map(params![limit], |row| {
            Ok(SessionEvent {
                timestamp: row.get(0)?,
                event_type: row.get(1)?,
                detail: row.get(2)?,
            })
        })?;
        rows.collect()
    }
}
