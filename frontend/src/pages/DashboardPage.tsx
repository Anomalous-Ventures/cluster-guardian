import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  AlertTriangle,
  Gauge,
  ScanSearch,
  Shield,
  Zap,
} from 'lucide-react';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';

import { StatCard } from '@/components/dashboard/StatCard';
import { ServiceHealthGrid } from '@/components/dashboard/ServiceHealthGrid';
import { ScanTimeline } from '@/components/dashboard/ScanTimeline';
import { StatusPageWidget } from '@/components/dashboard/StatusPageWidget';
import { TimeAgo } from '@/components/shared/TimeAgo';

import { useHealthStatus, useHealthChecks } from '@/hooks/useHealth';
import { useGuardianWs } from '@/hooks/useGuardianWs';
import { api } from '@/lib/api';
import type { AuditEntry } from '@/lib/api';

export function DashboardPage() {
  const { data: health } = useHealthStatus();
  const { data: healthChecks } = useHealthChecks();
  const { events } = useGuardianWs();

  const { data: lastScan } = useQuery({
    queryKey: ['lastScan'],
    queryFn: () => api.getLastScan(),
    refetchInterval: 30_000,
  });

  const { data: auditLog } = useQuery({
    queryKey: ['auditLog'],
    queryFn: () => api.getAuditLog(),
    refetchInterval: 30_000,
  });

  const now = Date.now();
  const oneDayAgo = now - 24 * 60 * 60 * 1000;

  const recentAuditEntries: AuditEntry[] = Array.isArray(auditLog)
    ? auditLog
    : (auditLog as any)?.entries ?? [];

  const scans24h = recentAuditEntries.filter(
    (e) =>
      e.action === 'scan' &&
      new Date(e.timestamp).getTime() > oneDayAgo,
  ).length;

  const issues24h = recentAuditEntries.filter(
    (e) =>
      e.result === 'failure' &&
      new Date(e.timestamp).getTime() > oneDayAgo,
  ).length;

  const rateLimitRemaining =
    (auditLog as any)?.rate_limit?.remaining_actions ?? '--';

  const serviceResults = (healthChecks?.results ?? []).map((hc) => ({
    service: hc.service,
    healthy: hc.healthy,
    errors: hc.errors ?? [],
    warnings: hc.warnings ?? [],
    timestamp: hc.timestamp,
  }));

  const scanEntries = lastScan
    ? [
        {
          success: lastScan.success,
          summary: lastScan.summary,
          timestamp: lastScan.timestamp,
          audit_log: lastScan.audit_log ?? [],
        },
      ]
    : [];

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="Version"
          value={health?.version ?? '--'}
          description={health?.status ?? 'Loading...'}
          icon={<Shield className="h-4 w-4" />}
        />
        <StatCard
          title="Scans (24h)"
          value={scans24h}
          description="Cluster scans executed"
          icon={<ScanSearch className="h-4 w-4" />}
        />
        <StatCard
          title="Issues (24h)"
          value={issues24h}
          description="Failed actions detected"
          icon={<AlertTriangle className="h-4 w-4" />}
        />
        <StatCard
          title="Rate Limit"
          value={rateLimitRemaining}
          description="Actions remaining this hour"
          icon={<Gauge className="h-4 w-4" />}
        />
      </div>

      <Separator />

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Activity className="h-5 w-5" />
                Service Health
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ServiceHealthGrid results={serviceResults} />
            </CardContent>
          </Card>

          <StatusPageWidget />

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ScanSearch className="h-5 w-5" />
                Recent Scans
              </CardTitle>
            </CardHeader>
            <CardContent>
              {scanEntries.length > 0 ? (
                <ScanTimeline scans={scanEntries} />
              ) : (
                <p className="text-muted-foreground text-sm">
                  No scan data available yet.
                </p>
              )}
            </CardContent>
          </Card>
        </div>

        <div>
          <Card className="h-full">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Zap className="h-5 w-5" />
                Live Activity
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3 max-h-[600px] overflow-y-auto">
                {events.length === 0 && (
                  <p className="text-muted-foreground text-sm">
                    Waiting for events...
                  </p>
                )}
                {events
                  .slice()
                  .reverse()
                  .slice(0, 50)
                  .map((event) => (
                    <div
                      key={event.id}
                      className="flex items-start gap-2 border-b pb-2 last:border-0"
                    >
                      <Badge
                        variant={
                          event.type === 'error'
                            ? 'destructive'
                            : event.type === 'warning'
                              ? 'warning'
                              : 'secondary'
                        }
                        className="mt-0.5 shrink-0"
                      >
                        {event.type}
                      </Badge>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm truncate">
                          {typeof event.data === 'string'
                            ? event.data
                            : JSON.stringify(event.data)}
                        </p>
                        <TimeAgo
                          date={event.timestamp}
                          className="text-xs text-muted-foreground"
                        />
                      </div>
                    </div>
                  ))}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
