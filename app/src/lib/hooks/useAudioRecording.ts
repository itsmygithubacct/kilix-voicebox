import { useCallback, useEffect, useRef, useState } from 'react';
import { apiClient } from '@/lib/api/client';
import { useRecordingStore } from '@/stores/recordingStore';

interface UseAudioRecordingOptions {
  active?: boolean;
  maxDurationSeconds?: number;
  onRecordingComplete?: (blob: Blob, duration?: number) => void;
}

export function useAudioRecording({ active = true, maxDurationSeconds = 29,
  onRecordingComplete }: UseAudioRecordingOptions = {}) {
  const [isRecording, setIsRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const status = useRecordingStore((state) => state.status);
  const current = useRef<{ id: string; controller: AbortController; stop: boolean } | null>(null);
  const callback = useRef(onRecordingComplete);
  callback.current = onRecordingComplete;
  const activeRef = useRef(active);
  activeRef.current = active;

  const cancelRecording = useCallback(() => {
    const job = current.current;
    if (!job) return;
    job.controller.abort();
    // Original request disconnect remains authoritative if this arrives first.
    void apiClient.controlRecording(job.id, 'cancel').catch(() => {});
  }, []);

  const stopRecording = useCallback(() => {
    const job = current.current;
    if (!job) return;
    job.stop = true;
    void apiClient.controlRecording(job.id, 'stop').catch(() => {});
  }, []);

  useEffect(() => {
    if (status.id === current.current?.id && status.phase === 'recording' && current.current.stop) {
      stopRecording();
    }
  }, [status, stopRecording]);

  const startRecording = useCallback(async () => {
    if (current.current || !activeRef.current) return;
    const job = { id: crypto.randomUUID().replace(/-/g, ''), controller: new AbortController(), stop: false };
    current.current = job;
    useRecordingStore.getState().begin(job.id);
    setError(null);
    setIsRecording(true);
    try {
      const result = await apiClient.recordAudio(job.id, maxDurationSeconds, job.controller.signal);
      if (!job.controller.signal.aborted && activeRef.current && current.current === job) {
        callback.current?.(result.blob, result.duration);
      }
    } catch {
      if (!job.controller.signal.aborted && activeRef.current) {
        setError('Recording refused. Check microphone availability and session lock state.');
      }
    } finally {
      if (current.current === job) {
        current.current = null;
        setIsRecording(false);
      }
    }
  }, [maxDurationSeconds]);

  useEffect(() => {
    if (!active) cancelRecording();
  }, [active, cancelRecording]);

  useEffect(() => {
    activeRef.current = active;
    const hidden = () => { if (document.hidden) cancelRecording(); };
    window.addEventListener('pagehide', cancelRecording);
    document.addEventListener('visibilitychange', hidden);
    return () => {
      activeRef.current = false;
      cancelRecording();
      window.removeEventListener('pagehide', cancelRecording);
      document.removeEventListener('visibilitychange', hidden);
    };
  }, [active, cancelRecording]);

  return { isRecording, duration: status.duration, error,
    startRecording, stopRecording, cancelRecording };
}
