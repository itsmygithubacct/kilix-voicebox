import type { UseFormReturn } from 'react-hook-form';
import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import type { GenerationFormValues } from '@/lib/hooks/useGenerationForm';

const BUILT_IN_VOICES = [
  'Vivian',
  'Serena',
  'Uncle_Fu',
  'Dylan',
  'Eric',
  'Ryan',
  'Aiden',
  'Ono_Anna',
  'Sohee',
];

export function VoiceModeFields({ form }: { form: UseFormReturn<GenerationFormValues> }) {
  const mode = form.watch('mode');
  return (
    <div className="space-y-3">
      <FormField
        control={form.control}
        name="mode"
        render={({ field }) => (
          <FormItem>
            <FormLabel>Voice source</FormLabel>
            <Select onValueChange={field.onChange} value={field.value}>
              <FormControl>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
              </FormControl>
              <SelectContent>
                <SelectItem value="prompt_clone">Consented sample</SelectItem>
                <SelectItem value="named_voice">Built-in voice</SelectItem>
                <SelectItem value="voice_design">Describe a voice</SelectItem>
              </SelectContent>
            </Select>
            <FormDescription>
              {mode === 'prompt_clone'
                ? 'Use the selected profile’s voice sample with the speaker’s permission.'
                : 'No voice sample is needed. The selected profile groups your generation history.'}
            </FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
      {mode === 'named_voice' && (
        <FormField
          control={form.control}
          name="voiceId"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Built-in voice</FormLabel>
              <Select onValueChange={field.onChange} value={field.value}>
                <FormControl>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {BUILT_IN_VOICES.map((voice) => (
                    <SelectItem key={voice} value={voice}>
                      {voice.replace(/_/g, ' ')}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FormDescription>Requires an installed CustomVoice model.</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
      )}
      {mode === 'voice_design' && (
        <FormField
          control={form.control}
          name="description"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Voice description</FormLabel>
              <FormControl>
                <Textarea
                  placeholder="A warm adult voice with a relaxed pace and clear pronunciation"
                  {...field}
                />
              </FormControl>
              <FormDescription>
                Describe vocal qualities. Requires an installed VoiceDesign model.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
      )}
    </div>
  );
}
