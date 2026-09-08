import { useMutation } from '@tanstack/react-query';
import { useCallback, useEffect, useRef } from 'react';
import { apiClient } from '@/lib/api/client';
import type { LanguageCode } from '@/lib/constants/languages';

export function useTranscription(active = true) {
  const owned = useRef<AbortController | null>(null);
  const enabled = useRef(active);
  enabled.current = active;
  const cancel = useCallback(() => { owned.current?.abort(); }, []);
  useEffect(() => {
    const hidden = () => { if (document.hidden) cancel(); };
    if (!active) cancel();
    window.addEventListener('pagehide', cancel);
    document.addEventListener('visibilitychange', hidden);
    return () => {
      cancel();
      window.removeEventListener('pagehide', cancel);
      document.removeEventListener('visibilitychange', hidden);
    };
  }, [active, cancel]);
  const mutation = useMutation({
    mutationFn: async ({ file, language }: { file: File; language?: LanguageCode }) => {
      cancel();
      if (!enabled.current) throw new Error('TRANSCRIPTION_STOPPED');
      const controller = new AbortController();
      owned.current = controller;
      try {
        const result = await apiClient.transcribeAudio(file, language, controller.signal);
        if (controller.signal.aborted || !enabled.current) throw new Error('TRANSCRIPTION_STOPPED');
        return result;
      } catch {
        throw new Error('TRANSCRIPTION_REFUSED');
      } finally {
        if (owned.current === controller) owned.current = null;
      }
    },
  });
  return { ...mutation, cancel };
}
