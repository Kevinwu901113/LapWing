/// Clipboard monitoring.
/// Windows: uses AddClipboardFormatListener.
/// Linux/macOS: no-op stub.

#[cfg(target_os = "windows")]
pub async fn run() {
    // TODO: Create hidden window, AddClipboardFormatListener, handle WM_CLIPBOARDUPDATE
    loop {
        tokio::time::sleep(std::time::Duration::from_secs(3600)).await;
    }
}

#[cfg(not(target_os = "windows"))]
pub async fn run() {
    log::info!("Clipboard monitor: stub (not on Windows)");
    loop {
        tokio::time::sleep(std::time::Duration::from_secs(3600)).await;
    }
}
