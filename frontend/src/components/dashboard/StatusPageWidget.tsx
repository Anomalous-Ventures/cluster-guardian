import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Activity } from 'lucide-react';
import { api } from '@/lib/api';

export function StatusPageWidget() {
  const { data } = useQuery({
    queryKey: ['statusPage'],
    queryFn: () => api.getStatusPage(),
    refetchInterval: 60_000,
  });

  const endpoints = data?.endpoints ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity className="h-5 w-5" />
          Status Page
        </CardTitle>
      </CardHeader>
      <CardContent>
        {endpoints.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No status page data available.
          </p>
        ) : (
          <div className="grid gap-2">
            {endpoints.map((ep) => (
              <div
                key={`${ep.group}/${ep.name}`}
                className="flex items-center justify-between rounded-md border px-3 py-2"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`inline-block h-2.5 w-2.5 rounded-full ${
                      ep.healthy ? 'bg-green-500' : 'bg-red-500'
                    }`}
                  />
                  <span className="text-sm font-medium">
                    {ep.group ? `${ep.group} / ` : ''}
                    {ep.name}
                  </span>
                </div>
                <span className="text-xs text-muted-foreground">
                  {ep.uptime_7d}%
                </span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
