import { Mic, Pause, Play, Square } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { FormControl, FormItem, FormMessage } from '@/components/ui/form';
import { formatAudioDuration } from '@/lib/utils/audio';

interface AudioSampleRecordingProps {
  file: File | null | undefined;
  isRecording: boolean;
  duration: number;
  onStart: () => void;
  onStop: () => void;
  onCancel: () => void;
  onTranscribe: () => void;
  onPlayPause: () => void;
  isPlaying: boolean;
  isTranscribing?: boolean;
}

export function AudioSampleRecording({
  file,
  isRecording,
  duration,
  onStart,
  onStop,
  onCancel,
  onTranscribe,
  onPlayPause,
  isPlaying,
  isTranscribing = false,
}: AudioSampleRecordingProps) {
  return (
    <FormItem>
      <FormControl>
        <div className="space-y-4">
          {!isRecording && !file && (
            <div className="relative flex flex-col items-center justify-center gap-4 p-4 border-2 border-dashed rounded-lg min-h-[180px] overflow-hidden">
              <Button
                type="button"
                onClick={onStart}
                size="lg"
                className="relative z-10 flex items-center gap-2"
              >
                <Mic className="h-5 w-5" />
                Start Recording
              </Button>
              <p className="relative z-10 text-sm text-muted-foreground text-center">
                Click to use the shared system microphone for up to 30 seconds.
                Recording does not grant permission to clone a voice.
              </p>
            </div>
          )}

          {isRecording && (
            <div className="relative flex flex-col items-center justify-center gap-4 p-4 border-2 border-accent rounded-lg bg-accent/5 min-h-[180px] overflow-hidden">
              <div className="relative z-10 flex items-center gap-4">
                <div className="flex items-center gap-2">
                  <div className="h-3 w-3 rounded-full bg-accent animate-pulse" />
                  <span className="text-lg font-mono font-semibold">
                    {formatAudioDuration(duration)}
                  </span>
                </div>
              </div>
              <Button
                type="button"
                onClick={onStop}
                className="relative z-10 flex items-center gap-2 bg-accent text-accent-foreground hover:bg-accent/90"
              >
                <Square className="h-4 w-4" />
                Stop Recording
              </Button>
              <p className="relative z-10 text-sm text-muted-foreground text-center">
                {formatAudioDuration(30 - duration)} remaining
              </p>
            </div>
          )}

          {file && !isRecording && (
            <div className="flex flex-col items-center justify-center gap-4 p-4 border-2 border-primary rounded-lg bg-primary/5 min-h-[180px]">
              <div className="flex items-center gap-2">
                <Mic className="h-5 w-5 text-primary" />
                <span className="font-medium">Recording complete</span>
              </div>
              <p className="text-sm text-muted-foreground text-center">File: {file.name}</p>
              <div className="flex gap-2">
                <Button type="button" size="icon" variant="outline" onClick={onPlayPause}>
                  {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={onTranscribe}
                  disabled={isTranscribing}
                  className="flex items-center gap-2"
                >
                  <Mic className="h-4 w-4" />
                  {isTranscribing ? 'Transcribing...' : 'Transcribe'}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={onCancel}
                  className="flex items-center gap-2"
                >
                  Record Again
                </Button>
              </div>
            </div>
          )}
        </div>
      </FormControl>
      <FormMessage />
    </FormItem>
  );
}
