import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { LOCAL_SERVER_URL } from '@/stores/serverStore';

export function ConnectionForm() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Local Service</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-1">
          <p className="text-sm font-medium">Loopback endpoint</p>
          <p className="font-mono text-sm">{LOCAL_SERVER_URL}</p>
          <p className="text-sm text-muted-foreground">
            Voicebox accepts product requests on this device only. LAN and remote endpoints are not
            supported.
          </p>
        </div>

        <div className="mt-6 pt-6 border-t space-y-1">
          <p className="text-sm font-medium">Application-owned lifecycle</p>
          <p className="text-sm text-muted-foreground">
            Voicebox starts one bundled orchestrator and stops that exact child when the application
            closes. Speech providers have independent lifecycles.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
