import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { FileText, List, ScrollText } from 'lucide-react';

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';

import { LogViewer } from '@/components/logs/LogViewer';
import { LogFilters } from '@/components/logs/LogFilters';
import { TimeAgo } from '@/components/shared/TimeAgo';

import { api } from '@/lib/api';
import type { LogLabels } from '@/lib/api';

interface LogFilterState {
  namespace?: string;
  pod?: string;
  container?: string;
  severity?: string;
  query?: string;
  since?: string;
}

export function LogsPage() {
  const [activeTab, setActiveTab] = useState('application');
  const [filters, setFilters] = useState<LogFilterState>({});
  const [eventsNamespace, setEventsNamespace] = useState<string | undefined>();

  const { data: labels } = useQuery<LogLabels>({
    queryKey: ['logLabels'],
    queryFn: () => api.getLogLabels(),
  });

  const { data: logs = [], isLoading: logsLoading } = useQuery({
    queryKey: ['logs', filters],
    queryFn: () => api.getLogs(filters),
    select: (data) => data.entries,
    refetchInterval: 15_000,
  });

  const { data: events = [], isLoading: eventsLoading } = useQuery({
    queryKey: ['events', eventsNamespace],
    queryFn: () => api.getEvents(eventsNamespace),
    select: (data) => data.events,
    refetchInterval: 15_000,
  });

  const { data: guardianLogs = [], isLoading: guardianLoading } = useQuery({
    queryKey: ['guardianLogs'],
    queryFn: () =>
      api.getLogs({ pod: 'cluster-guardian', since: '1h' }),
    select: (data) => data.entries,
    refetchInterval: 15_000,
  });

  const handleFilterChange = (newFilters: LogFilterState) => {
    setFilters(newFilters);
  };

  const eventTypeVariant = (type: string) => {
    switch (type.toLowerCase()) {
      case 'warning':
        return 'warning' as const;
      case 'error':
        return 'destructive' as const;
      default:
        return 'secondary' as const;
    }
  };

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold tracking-tight">Logs</h1>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="application" className="flex items-center gap-1">
            <FileText className="h-4 w-4" />
            Application Logs
          </TabsTrigger>
          <TabsTrigger value="events" className="flex items-center gap-1">
            <List className="h-4 w-4" />
            K8s Events
          </TabsTrigger>
          <TabsTrigger value="guardian" className="flex items-center gap-1">
            <ScrollText className="h-4 w-4" />
            Guardian Logs
          </TabsTrigger>
        </TabsList>

        <TabsContent value="application" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Filters</CardTitle>
            </CardHeader>
            <CardContent>
              <LogFilters
                namespaces={labels?.namespaces ?? []}
                pods={labels?.pods ?? []}
                containers={labels?.containers ?? []}
                onFilterChange={handleFilterChange}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>
                Application Logs
                <span className="ml-2 text-sm font-normal text-muted-foreground">
                  ({logs.length} entries)
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <LogViewer entries={logs} loading={logsLoading} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="events" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Filters</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center gap-4">
                <label className="text-sm font-medium">Namespace</label>
                <select
                  className="rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={eventsNamespace ?? ''}
                  onChange={(e) =>
                    setEventsNamespace(e.target.value || undefined)
                  }
                >
                  <option value="">All namespaces</option>
                  {(labels?.namespaces ?? []).map((ns) => (
                    <option key={ns} value={ns}>
                      {ns}
                    </option>
                  ))}
                </select>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>
                Kubernetes Events
                <span className="ml-2 text-sm font-normal text-muted-foreground">
                  ({events.length} events)
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {eventsLoading ? (
                <p className="text-sm text-muted-foreground">
                  Loading events...
                </p>
              ) : events.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No events found.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left">
                        <th className="pb-2 pr-4 font-medium">Time</th>
                        <th className="pb-2 pr-4 font-medium">Type</th>
                        <th className="pb-2 pr-4 font-medium">Reason</th>
                        <th className="pb-2 pr-4 font-medium">Object</th>
                        <th className="pb-2 pr-4 font-medium">Namespace</th>
                        <th className="pb-2 font-medium">Message</th>
                      </tr>
                    </thead>
                    <tbody>
                      {events.map((event, idx) => (
                        <tr key={idx} className="border-b last:border-0">
                          <td className="py-2 pr-4 whitespace-nowrap">
                            <TimeAgo
                              date={event.timestamp}
                              className="text-muted-foreground"
                            />
                          </td>
                          <td className="py-2 pr-4">
                            <Badge variant={eventTypeVariant(event.type)}>
                              {event.type}
                            </Badge>
                          </td>
                          <td className="py-2 pr-4 whitespace-nowrap">
                            {event.reason}
                          </td>
                          <td className="py-2 pr-4 font-mono text-xs">
                            {event.object}
                          </td>
                          <td className="py-2 pr-4">
                            <Badge variant="outline">{event.namespace}</Badge>
                          </td>
                          <td className="py-2 max-w-md truncate">
                            {event.message}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="guardian" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>
                Guardian Logs
                <span className="ml-2 text-sm font-normal text-muted-foreground">
                  ({guardianLogs.length} entries)
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <LogViewer entries={guardianLogs} loading={guardianLoading} />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
