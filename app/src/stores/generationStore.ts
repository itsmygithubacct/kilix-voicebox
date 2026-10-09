import { create } from 'zustand';

interface GenerationState {
  isGenerating: boolean;
  activeGenerationId: string | null;
  cancelGeneration: (() => void) | null;
  setIsGenerating: (generating: boolean) => void;
  setActiveGenerationId: (id: string | null) => void;
}

export const useGenerationStore = create<GenerationState>((set) => ({
  isGenerating: false,
  activeGenerationId: null,
  cancelGeneration: null,
  setIsGenerating: (generating) => set({ isGenerating: generating }),
  setActiveGenerationId: (id) => set({ activeGenerationId: id }),
}));
