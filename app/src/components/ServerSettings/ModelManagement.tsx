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
    </Card>
  );
}
