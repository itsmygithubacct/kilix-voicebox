use crate::audio_capture::AudioCaptureState;

pub async fn start_capture(
    _state: &AudioCaptureState,
    _max_duration_secs: u32,
) -> Result<(), String> {
    Err("VOICEBOX_SYSTEM_AUDIO_UNSUPPORTED".to_string())
}

pub async fn stop_capture(_state: &AudioCaptureState) -> Result<String, String> {
    Err("VOICEBOX_SYSTEM_AUDIO_UNSUPPORTED".to_string())
}

pub fn is_supported() -> bool {
    false
}
