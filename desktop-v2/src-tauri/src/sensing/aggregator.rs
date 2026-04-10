/// Data aggregator: periodically summarizes sensing data and POSTs to the server.

use std::sync::Arc;
use parking_lot::Mutex;
use super::db::SensingDb;

const INTERVAL_SECONDS: u64 = 180; // 3 minutes

pub async fn run(db: Arc<Mutex<SensingDb>>, server_url: String) {
    loop {
        tokio::time::sleep(std::time::Duration::from_secs(INTERVAL_SECONDS)).await;

        let summary = build_summary(&db);
        if summary.is_empty() {
            continue;
        }

        let url = format!("{}/api/sensing/context", server_url.trim_end_matches('/'));
        let body = serde_json::json!({
            "summary": summary,
            "state": "normal",
            "timestamp": chrono::Utc::now().to_rfc3339(),
        });

        match reqwest::Client::new()
            .post(&url)
            .json(&body)
            .send()
            .await
        {
            Ok(resp) if resp.status().is_success() => {
                log::debug!("Sensing context pushed to server");
            }
            Ok(resp) => {
                log::warn!("Sensing push failed: HTTP {}", resp.status());
            }
            Err(e) => {
                log::debug!("Sensing push error (server may be offline): {e}");
            }
        }
    }
}

fn build_summary(db: &Arc<Mutex<SensingDb>>) -> String {
    let db = match db.try_lock() {
        Some(d) => d,
        None => return String::new(),
    };

    let usage = db.get_app_usage_today().unwrap_or_default();
    if usage.is_empty() {
        return String::new();
    }

    let mut parts = Vec::new();

    // Top apps today
    let top: Vec<String> = usage.iter().take(5).map(|u| {
        let mins = u.total_seconds / 60;
        let name = if u.app_display_name.is_empty() {
            &u.process_name
        } else {
            &u.app_display_name
        };
        if mins >= 60 {
            format!("{} {}h{}m", name, mins / 60, mins % 60)
        } else {
            format!("{} {}m", name, mins)
        }
    }).collect();

    parts.push(format!("今日应用使用：{}", top.join(", ")));

    parts.join("\n")
}
