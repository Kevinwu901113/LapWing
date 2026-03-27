#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::env;
use std::fs;
use std::path::PathBuf;

#[tauri::command]
fn read_bootstrap_token() -> Result<String, String> {
    let lapwing_home = env::var_os("LAPWING_HOME")
        .map(PathBuf::from)
        .or_else(|| env::var_os("HOME").map(|home| PathBuf::from(home).join(".lapwing")))
        .or_else(|| env::var_os("USERPROFILE").map(|home| PathBuf::from(home).join(".lapwing")))
        .ok_or_else(|| "无法确定 LAPWING_HOME 或 HOME".to_string())?;

    let token_path = lapwing_home.join("auth").join("api-bootstrap-token");
    let token = fs::read_to_string(&token_path)
        .map_err(|err| format!("读取 bootstrap token 失败 ({}): {}", token_path.display(), err))?;
    let trimmed = token.trim();
    if trimmed.is_empty() {
        return Err(format!("bootstrap token 为空: {}", token_path.display()));
    }
    Ok(trimmed.to_string())
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![read_bootstrap_token])
        .run(tauri::generate_context!())
        .expect("error while running Lapwing desktop");
}
