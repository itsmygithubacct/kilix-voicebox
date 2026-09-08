import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import * as z from 'zod';
import { useToast } from '@/components/ui/use-toast';
import { apiClient } from '@/lib/api/client';
import { LANGUAGE_CODES, type LanguageCode } from '@/lib/constants/languages';
import { useGeneration } from '@/lib/hooks/useGeneration';
import { useGenerationStore } from '@/stores/generationStore';
import { usePlayerStore } from '@/stores/playerStore';

const generationSchema = z
  .object({
    text: z.string().min(1, 'Text is required').max(5000),
    language: z.enum(LANGUAGE_CODES as [LanguageCode, ...LanguageCode[]]),
    seed: z.number().int().optional(),
    instruct: z.string().max(500).optional(),
    consentAsserted: z.boolean(),
    mode: z.enum(['prompt_clone', 'named_voice', 'voice_design']),
    voiceId: z.string().max(64),
    description: z.string().max(4096),
  })
  .superRefine((value, context) => {
    if (value.mode === 'prompt_clone' && !value.consentAsserted) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['consentAsserted'],
        message: 'Confirm that you have permission to use this voice sample.',
      });
    }
    if (value.mode === 'named_voice' && !value.voiceId.trim()) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['voiceId'],
        message: 'Select a built-in voice.',
      });
    }
    if (value.mode === 'voice_design' && !value.description.trim()) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['description'],
        message: 'Describe the voice you want to create.',
      });
    }
  });

export type GenerationFormValues = z.infer<typeof generationSchema>;

interface UseGenerationFormOptions {
  onSuccess?: (generationId: string) => void;
  defaultValues?: Partial<GenerationFormValues>;
}

export function useGenerationForm(options: UseGenerationFormOptions = {}) {
  const { toast } = useToast();
  const generation = useGeneration();
  const setAudioWithAutoPlay = usePlayerStore((state) => state.setAudioWithAutoPlay);
  const setIsGenerating = useGenerationStore((state) => state.setIsGenerating);
  const form = useForm<GenerationFormValues>({
    resolver: zodResolver(generationSchema),
    defaultValues: {
      text: '',
      language: 'en',
      seed: undefined,
      instruct: '',
      consentAsserted: false,
      mode: 'prompt_clone',
      voiceId: 'Vivian',
      description: '',
      ...options.defaultValues,
    },
  });

  async function handleSubmit(
    data: GenerationFormValues,
    selectedProfileId: string | null,
  ): Promise<void> {
    if (!selectedProfileId) {
      toast({
        title: 'No profile selected',
        description: 'Please select a voice profile from the cards above.',
        variant: 'destructive',
      });
      return;
    }

    try {
      setIsGenerating(true);

      const result = await generation.mutateAsync({
        profile_id: selectedProfileId,
        text: data.text,
        language: data.language,
        seed: data.seed,
        instruct: data.instruct || undefined,
        consent_asserted: data.mode === 'prompt_clone' && data.consentAsserted,
        mode: data.mode,
        voice_id: data.mode === 'named_voice' ? data.voiceId : undefined,
        description: data.mode === 'voice_design' ? data.description : undefined,
      });

      toast({
        title: 'Generation complete!',
        description: `Audio generated (${result.duration.toFixed(2)}s)`,
      });

      const audioUrl = apiClient.getAudioUrl(result.id);
      setAudioWithAutoPlay(audioUrl, result.id, selectedProfileId, data.text.substring(0, 50));

      form.reset();
      options.onSuccess?.(result.id);
    } catch (error) {
      toast({
        title: 'Generation failed',
        description: error instanceof Error ? error.message : 'Failed to generate audio',
        variant: 'destructive',
      });
    } finally {
      setIsGenerating(false);
    }
  }

  return {
    form,
    handleSubmit,
    isPending: generation.isPending,
  };
}
