/// Game process detection.
/// Windows: enumerates running processes to find known games.
/// Linux/macOS: no-op stub.

#[allow(dead_code)]
pub const KNOWN_GAMES: &[&str] = &[
    "cs2.exe", "csgo.exe",
    "valorant.exe", "VALORANT-Win64-Shipping.exe",
    "dota2.exe",
    "LeagueClient.exe", "League of Legends.exe",
    "GenshinImpact.exe", "YuanShen.exe",
];

#[allow(dead_code)]
pub const ANTICHEAT_PROCESSES: &[&str] = &[
    "vgc.exe",
    "EasyAntiCheat.exe",
    "BEService.exe",
    "5EClient.exe",
    "PerfectWorld.exe",
];

pub fn is_game_running() -> bool {
    #[cfg(target_os = "windows")]
    {
        // TODO: Enumerate processes and check against KNOWN_GAMES + ANTICHEAT_PROCESSES
        false
    }
    #[cfg(not(target_os = "windows"))]
    {
        false
    }
}

pub fn get_current_game() -> Option<String> {
    #[cfg(target_os = "windows")]
    {
        // TODO: Return the name of the currently running game
        None
    }
    #[cfg(not(target_os = "windows"))]
    {
        None
    }
}
