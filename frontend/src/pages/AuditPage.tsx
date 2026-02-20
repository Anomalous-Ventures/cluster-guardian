import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Clock, Filter, Gauge, ShieldAlert } from 'lucide-react';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

import { StatCard } from '@/components/dashboard/StatCard';
import { TimeAgo } from '@/components/shared/TimeAgo';

import { api } from '@/lib/api';
import type { AuditEntry } from '@/lib/api';

export function AuditPage() {
  const [actionFilter, setActionFilter] = useState('');
  const [namespaceFilter, setNamespaceFilter] = useState('');
  const [resultFilter, setResultFilter] = useState('');

  const { data: auditResponse } = useQuery({
    queryKey: ['auditLog'],
    queryFn: () => api.getAuditLog(),
    refetchInterval: 15_000,
  });

  const entries: AuditEntry[] = Array.isArray(auditResponse)
    ? auditResponse
    : (auditResponse as any)?.entries ?? [];

  const rateLimit: Record<string, number> =
    (auditResponse as any)?.rate_limit ?? {};

  const actions = useMemo(
    () => [...new Set(entries.map((e) => e.action).filter(Boolean))],
    [entries],
  );

  const namespaces = useMemo(
    () => [...new Set(entries.map((e) => e.namespace).filter(Boolean))],
    [entries],
  );

  const results = useMemo(
    () => [...new Set(entries.map((e) => e.result).filter(Boolean))],
    [entries],
  );

  const filtered = useMemo(() => {
    return entries.filter((entry) => {
      if (actionFilter && entry.action !== actionFilter) return false;
      if (namespaceFilter && entry.namespace !== namespaceFilter) return false;
      if (resultFilter && entry.result !== resultFilter) return false;
      return true;
    });
  }, [entries, actionFilter, namespaceFilter, resultFilter]);

  const resultVariant = (result?: string) => {
    switch (result?.toLowerCase()) {
      case 'success':
        return 'healthy' as const;
      case 'failure':
      case 'failed':
        return 'destructive' as const;
      case 'skipped':
        return 'secondary' as const;
      default:
        return 'outline' as const;
    }
  };

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold tracking-tight">Audit Log</h1>

      <div className="grid gap-4 md:grid-cols-3">
        <StatCard
          title="Total Actions"
          value={entries.length}
          description="All recorded actions"
          icon={<ShieldAlert className="h-4 w-4" />}
        />
        <StatCard
          title="Max Per Hour"
          value={rateLimit.max_actions_per_hour ?? '--'}
          description="Maximum actions allowed"
          icon={<Gauge className="h-4 w-4" />}
        />
        <StatCard
          title="Remaining"
          value={rateLimit.remaining_actions ?? '--'}
          description="Actions available this hour"
          icon={<Clock className="h-4 w-4" />}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Filter className="h-5 w-5" />
            Filters
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-4">
            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">
                Action
              </label>
              <select
                className="rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={actionFilter}
                onChange={(e) => setActionFilter(e.target.value)}
              >
                <option value="">All actions</option>
                {actions.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">
                Namespace
              </label>
              <select
                className="rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={namespaceFilter}
                onChange={(e) => setNamespaceFilter(e.target.value)}
              >
                <option value="">All namespaces</option>
                {namespaces.map((ns) => (
                  <option key={ns} value={ns}>
                    {ns}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">
                Result
              </label>
              <select
                className="rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={resultFilter}
                onChange={(e) => setResultFilter(e.target.value)}
              >
                <option value="">All results</option>
                {results.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>
            Audit Entries
            <span className="ml-2 text-sm font-normal text-muted-foreground">
              ({filtered.length} of {entries.length})
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {filtered.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">
              No audit entries match the current filters.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left">
                    <th className="pb-2 pr-4 font-medium">Timestamp</th>
                    <th className="pb-2 pr-4 font-medium">Action</th>
                    <th className="pb-2 pr-4 font-medium">Target</th>
                    <th className="pb-2 pr-4 font-medium">Namespace</th>
                    <th className="pb-2 pr-4 font-medium">Reason</th>
                    <th className="pb-2 font-medium">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((entry, idx) => (
                    <tr key={idx} className="border-b last:border-0">
                      <td className="py-2 pr-4 whitespace-nowrap">
                        <TimeAgo
                          date={entry.timestamp}
                          className="text-muted-foreground"
                        />
                      </td>
                      <td className="py-2 pr-4">
                        <Badge variant="secondary">{entry.action}</Badge>
                      </td>
                      <td className="py-2 pr-4 font-mono text-xs">
                        {entry.target ?? '--'}
                      </td>
                      <td className="py-2 pr-4">
                        {entry.namespace ? (
                          <Badge variant="outline">{entry.namespace}</Badge>
                        ) : (
                          '--'
                        )}
                      </td>
                      <td className="py-2 pr-4 max-w-xs truncate">
                        {entry.reason ?? '--'}
                      </td>
                      <td className="py-2">
                        {entry.result ? (
                          <Badge variant={resultVariant(entry.result)}>
                            {entry.result}
                          </Badge>
                        ) : (
                          '--'
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
