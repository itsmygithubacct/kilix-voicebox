import { useEffect } from 'react';
import { apiClient } from '@/lib/api/client';
import { useRecordingStore } from '@/stores/recordingStore';

export function RecordingIndicator() {
  const status = useRecordingStore((state) => state.status);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const status = await apiClient.recordingStatus(controller.signal);
        if (!controller.signal.aborted) useRecordingStore.getState().refresh(status);
      } catch {
        if (!controller.signal.aborted) useRecordingStore.getState().uncertain();
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 250);
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, []);
  if (status.phase === 'inactive') return null;
  const label = {
    starting: 'Opening shared microphone…',
    recording: 'Microphone recording',
    stopping: 'Stopping microphone — waiting for cleanup',
    unavailable: 'Microphone status unavailable — recording is refused if cleanup is uncertain',
  }[status.phase];
  return <output aria-live="polite" data-testid="recording-indicator"
    className="fixed bottom-2 left-2 z-[100] rounded border bg-background px-3 py-2 text-sm">
    {label}
  </output>;
}
