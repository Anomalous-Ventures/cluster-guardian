import { Loader2, RefreshCw, Server } from 'lucide-react';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

import { ServiceHealthGrid } from '@/components/dashboard/ServiceHealthGrid';
import { useHealthChecks } from '@/hooks/useHealth';

export function ServicesPage() {
  const { data: healthChecks, isLoading, refetch, isRefetching } = useHealthChecks();

  const serviceResults = (healthChecks?.results ?? []).map((hc) => ({
    service: hc.service,
    healthy: hc.healthy,
    errors: hc.errors ?? [],
    warnings: hc.warnings ?? [],
    timestamp: hc.timestamp,
  }));

  const healthyCount = serviceResults.filter((s: { healthy: boolean }) => s.healthy).length;
  const totalCount = serviceResults.length;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Services</h1>
          <p className="text-muted-foreground mt-1">
            {totalCount > 0
              ? `${healthyCount} of ${totalCount} services healthy`
              : 'Loading service health data...'}
          </p>
        </div>
        <Button
          onClick={() => refetch()}
          disabled={isRefetching}
        >
          {isRefetching ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="mr-2 h-4 w-4" />
          )}
          Run Health Checks
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Server className="h-5 w-5" />
            All Services
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : serviceResults.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-12">
              No service health data available. Run health checks to get
              started.
            </p>
          ) : (
            <ServiceHealthGrid results={serviceResults} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
