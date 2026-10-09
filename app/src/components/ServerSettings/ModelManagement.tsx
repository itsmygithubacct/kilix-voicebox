import { useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { apiClient } from '@/lib/api/client';

export function ModelManagement() {
  const {
    data: modelStatus,
    isLoading,
    error,
  } = useQuery({
    queryKey: ['modelStatus'],
    queryFn: () => apiClient.getModelStatus(),
    refetchInterval: 5000,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Speech Provider Inventory</CardTitle>
        <CardDescription>
          Provider-reported state only. Kilix Content owns model installation, licensing, and
          removal.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading ? (
          <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Checking 2/2 providers
          </div>
        ) : error ? (
          <p className="text-sm text-destructive">Provider inventory is unavailable.</p>
        ) : modelStatus?.models.length ? (
          modelStatus.models.map((model) => (
            <div
              key={`${model.provider}:${model.model_id}`}
              className="flex items-center justify-between rounded-lg border p-3"
            >
              <div>
                <p className="text-sm font-medium">{model.display_name}</p>
                <p className="text-xs text-muted-foreground">
                  {model.provider} · {model.model_id}
                </p>
                {model.model_revision && <p className="text-xs break-all">Revision: {model.model_revision}</p>}
                <p className="text-xs text-muted-foreground">
                  {model.license_id ?? 'Terms are shown by Kilix Content'}
                  {model.download_bytes != null && ` · Download ${(model.download_bytes / 1024 ** 3).toFixed(2)} GiB`}
                  {model.temporary_bytes != null && ` · Temporary ${(model.temporary_bytes / 1024 ** 3).toFixed(2)} GiB`}
                </p>
                <p className="text-xs">{model.asset_authority === 'kilix-content'
                  ? 'Content authority selected; receipt and bytes are checked for every job.'
                  : 'No current Content admission reported.'} {model.release_qualified ? 'Qualified profile' : 'Profile qualification pending'}</p>
              </div>
              <div className="flex gap-2">
                <Badge variant={model.installed ? 'secondary' : 'outline'}>
                  {model.installed ? 'Installed' : 'Not installed'}
                </Badge>
                {model.resident && <Badge>Resident</Badge>}
              </div>
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">
            0/2 providers reported an installed model. Install an accepted speech profile through
            Kilix Content.
          </p>
        )}
      </CardContent>
      <p className="px-6 pb-6 text-sm text-muted-foreground">
        Select the matching model with Voice source: Base for a consented sample, CustomVoice for a built-in voice,
        or VoiceDesign for a description. Use Kilix Content to review full model terms and size before agreeing;
        then explicitly prepare and start the shared provider. Model agreement does not grant sample consent.
        CustomVoice 0.6B and Base do not support generation instructions. Transcription uses the installed Tiny profile;
        this package does not claim diarization or live transcript updates.
      </p>
    </Card>
  );
}
