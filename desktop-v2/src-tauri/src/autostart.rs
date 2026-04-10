/// Autostart configuration using tauri-plugin-autostart.
/// Cross-platform: works on Windows, macOS, and Linux.

pub fn is_minimized_start() -> bool {
    std::env::args().any(|arg| arg == "--minimized")
}
