import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api/client';
import type { GenerationRequest } from '@/lib/api/types';
import { useGenerationStore } from '@/stores/generationStore';

export function useGeneration() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (data: GenerationRequest) => {
      const controller = new AbortController();
      useGenerationStore.setState({ cancelGeneration: () => controller.abort() });
      try {
        return await apiClient.generateSpeech(data, controller.signal);
      } finally {
        useGenerationStore.setState({ cancelGeneration: null });
      }
    },
    onSuccess: () => {
      // Invalidate history to show new generation
      queryClient.invalidateQueries({ queryKey: ['history'] });
    },
  });
}
