// Prevents an additional console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod audio_capture;
mod audio_output;

use rand::{rngs::OsRng, RngCore};
use std::fmt::Write as _;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Mutex,
};
use tauri::{command, Manager, RunEvent, State, WindowEvent};
use tauri_plugin_shell::{process::CommandEvent, ShellExt};

const SERVER_PORT: u16 = 17_493;
const LOCAL_SERVER_URL: &str = "http://127.0.0.1:17493";

struct OwnedServer {
    generation: u64,
    session_token: String,
    child: tauri_plugin_shell::process::CommandChild,
}

#[derive(serde::Serialize)]
struct ServerSession {
    url: String,
    token: String,
}

struct ServerState {
    transition: tokio::sync::Mutex<()>,
    child: Mutex<Option<OwnedServer>>,
    next_generation: AtomicU64,
}

fn stop_owned_server(state: &ServerState) -> Result<(), String> {
    let mut slot = state
        .child
        .lock()
        .map_err(|_| "VOICEBOX_SIDECAR_STATE_FAILED".to_string())?;
    if let Some(server) = slot.take() {
        server
            .child
            .kill()
            .map_err(|_| "VOICEBOX_SIDECAR_STOP_FAILED".to_string())?;
    }
    Ok(())
}

fn new_session_token() -> Result<String, String> {
    let mut bytes = [0_u8; 32];
    OsRng
        .try_fill_bytes(&mut bytes)
        .map_err(|_| "VOICEBOX_SESSION_TOKEN_FAILED".to_string())?;
    let mut token = String::with_capacity(64);
    for byte in bytes {
        write!(&mut token, "{byte:02x}")
            .map_err(|_| "VOICEBOX_SESSION_TOKEN_FAILED".to_string())?;
    }
    Ok(token)
}

#[command]
async fn start_server(
    app: tauri::AppHandle,
    state: State<'_, ServerState>,
) -> Result<ServerSession, String> {
    let _transition = state.transition.lock().await;
    {
        let slot = state
            .child
            .lock()
            .map_err(|_| "VOICEBOX_SIDECAR_STATE_FAILED".to_string())?;
        if let Some(server) = slot.as_ref() {
            return Ok(ServerSession {
                url: LOCAL_SERVER_URL.to_string(),
                token: server.session_token.clone(),
            });
        }
    }

    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|_| "VOICEBOX_DATA_DIR_UNAVAILABLE".to_string())?;
    std::fs::create_dir_all(&data_dir).map_err(|_| "VOICEBOX_DATA_DIR_UNAVAILABLE".to_string())?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&data_dir, std::fs::Permissions::from_mode(0o700))
            .map_err(|_| "VOICEBOX_DATA_DIR_UNAVAILABLE".to_string())?;
    }

    let data_dir = data_dir
        .to_str()
        .ok_or_else(|| "VOICEBOX_DATA_DIR_UNAVAILABLE".to_string())?;
    let port = SERVER_PORT.to_string();
    let session_token = new_session_token()?;
    let sidecar = app
        .shell()
        .sidecar("voicebox-server")
        .map_err(|_| "VOICEBOX_SIDECAR_UNAVAILABLE".to_string())?
        .env("KILIX_VOICEBOX_SESSION_TOKEN", &session_token)
        .args(["--data-dir", data_dir, "--port", &port]);
    let (mut events, child) = sidecar
        .spawn()
        .map_err(|_| "VOICEBOX_SIDECAR_START_FAILED".to_string())?;
    let generation = state.next_generation.fetch_add(1, Ordering::Relaxed);
    match state.child.lock() {
        Ok(mut slot) => {
            *slot = Some(OwnedServer {
                generation,
                session_token: session_token.clone(),
                child,
            });
        }
        Err(_) => {
            let _ = child.kill();
            return Err("VOICEBOX_SIDECAR_STATE_FAILED".to_string());
        }
    }

    let ready = tokio::time::timeout(tokio::time::Duration::from_secs(120), async {
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                    let line = String::from_utf8_lossy(&line);
                    if line.contains("Uvicorn running")
                        || line.contains("Application startup complete")
                    {
                        return true;
                    }
                }
                CommandEvent::Error(_) | CommandEvent::Terminated(_) => return false,
                _ => {}
            }
        }
        false
    })
    .await
    .unwrap_or(false);

    if !ready {
        let _ = stop_owned_server(&state);
        return Err("VOICEBOX_SIDECAR_NOT_READY".to_string());
    }

    let app_handle = app.clone();
    tokio::spawn(async move {
        while events.recv().await.is_some() {}
        let state = app_handle.state::<ServerState>();
        if let Ok(mut slot) = state.child.lock() {
            if slot.as_ref().map(|server| server.generation) == Some(generation) {
                slot.take();
            }
        };
    });

    Ok(ServerSession {
        url: LOCAL_SERVER_URL.to_string(),
        token: session_token,
    })
}

#[command]
async fn stop_server(state: State<'_, ServerState>) -> Result<(), String> {
    let _transition = state.transition.lock().await;
    stop_owned_server(&state)
}

#[command]
async fn start_system_audio_capture(
    state: State<'_, audio_capture::AudioCaptureState>,
    max_duration_secs: u32,
) -> Result<(), String> {
    audio_capture::start_capture(&state, max_duration_secs)
        .await
        .map_err(|_| "VOICEBOX_AUDIO_CAPTURE_START_FAILED".to_string())
}

#[command]
async fn stop_system_audio_capture(
    state: State<'_, audio_capture::AudioCaptureState>,
) -> Result<String, String> {
    audio_capture::stop_capture(&state)
        .await
        .map_err(|_| "VOICEBOX_AUDIO_CAPTURE_STOP_FAILED".to_string())
}

#[command]
fn is_system_audio_supported() -> bool {
    audio_capture::is_supported()
}

#[command]
fn list_audio_output_devices(
    state: State<'_, audio_output::AudioOutputState>,
) -> Result<Vec<audio_output::AudioOutputDevice>, String> {
    state
        .list_output_devices()
        .map_err(|_| "VOICEBOX_AUDIO_DEVICE_LIST_FAILED".to_string())
}

#[command]
async fn play_audio_to_devices(
    state: State<'_, audio_output::AudioOutputState>,
    audio_data: Vec<u8>,
    device_ids: Vec<String>,
) -> Result<(), String> {
    state
        .play_audio_to_devices(audio_data, device_ids)
        .await
        .map_err(|_| "VOICEBOX_AUDIO_PLAYBACK_START_FAILED".to_string())
}

#[command]
fn stop_audio_playback(state: State<'_, audio_output::AudioOutputState>) -> Result<(), String> {
    state
        .stop_all_playback()
        .map_err(|_| "VOICEBOX_AUDIO_PLAYBACK_STOP_FAILED".to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .manage(ServerState {
            transition: tokio::sync::Mutex::new(()),
            child: Mutex::new(None),
            next_generation: AtomicU64::new(1),
        })
        .manage(audio_capture::AudioCaptureState::new())
        .manage(audio_output::AudioOutputState::new())
        .invoke_handler(tauri::generate_handler![
            start_server,
            stop_server,
            start_system_audio_capture,
            stop_system_audio_capture,
            is_system_audio_supported,
            list_audio_output_devices,
            play_audio_to_devices,
            stop_audio_playback
        ])
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let app = window.app_handle();
                let state = app.state::<ServerState>();
                let _ = stop_owned_server(&state);
                app.exit(0);
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build the Voicebox application")
        .run(|app, event| {
            if matches!(event, RunEvent::Exit) {
                let state = app.state::<ServerState>();
                let _ = stop_owned_server(&state);
            }
        });
}

fn main() {
    run();
}
