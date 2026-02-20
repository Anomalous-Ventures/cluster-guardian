import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Cable, Loader2 } from 'lucide-react';

import { Separator } from '@/components/ui/separator';

import { ConnectionCard } from '@/components/config/ConnectionCard';

import { api } from '@/lib/api';
import type { ConnectionStatus } from '@/lib/api';

interface ConnectionDef {
  name: string;
  displayName: string;
  fields: { key: string; label: string; type: 'text' | 'password' }[];
}

const NOTIFICATION_CHANNELS: ConnectionDef[] = [
  {
    name: 'slack',
    displayName: 'Slack',
    fields: [{ key: 'webhook_url', label: 'Webhook URL', type: 'text' }],
  },
  {
    name: 'discord',
    displayName: 'Discord',
    fields: [
      { key: 'discord_webhook_url', label: 'Webhook URL', type: 'text' },
    ],
  },
  {
    name: 'teams',
    displayName: 'Microsoft Teams',
    fields: [
      { key: 'teams_webhook_url', label: 'Webhook URL', type: 'text' },
    ],
  },
  {
    name: 'pagerduty',
    displayName: 'PagerDuty',
    fields: [
      {
        key: 'pagerduty_integration_key',
        label: 'Integration Key',
        type: 'password',
      },
    ],
  },
];

const TOOL_BACKENDS: ConnectionDef[] = [
  {
    name: 'prometheus',
    displayName: 'Prometheus',
    fields: [{ key: 'prometheus_url', label: 'URL', type: 'text' }],
  },
  {
    name: 'loki',
    displayName: 'Loki',
    fields: [{ key: 'loki_url', label: 'URL', type: 'text' }],
  },
  {
    name: 'alertmanager',
    displayName: 'AlertManager',
    fields: [{ key: 'alertmanager_url', label: 'URL', type: 'text' }],
  },
  {
    name: 'k8sgpt',
    displayName: 'K8sGPT',
    fields: [{ key: 'k8sgpt_url', label: 'URL', type: 'text' }],
  },
  {
    name: 'crowdsec',
    displayName: 'CrowdSec',
    fields: [
      { key: 'crowdsec_lapi_url', label: 'LAPI URL', type: 'text' },
      { key: 'crowdsec_api_key', label: 'API Key', type: 'password' },
    ],
  },
  {
    name: 'longhorn',
    displayName: 'Longhorn',
    fields: [{ key: 'longhorn_url', label: 'URL', type: 'text' }],
  },
  {
    name: 'redis',
    displayName: 'Redis',
    fields: [{ key: 'redis_url', label: 'URL', type: 'text' }],
  },
  {
    name: 'qdrant',
    displayName: 'Qdrant',
    fields: [
      { key: 'qdrant_url', label: 'URL', type: 'text' },
      { key: 'qdrant_collection', label: 'Collection', type: 'text' },
    ],
  },
  {
    name: 'langfuse',
    displayName: 'Langfuse',
    fields: [
      { key: 'langfuse_url', label: 'URL', type: 'text' },
      { key: 'langfuse_public_key', label: 'Public Key', type: 'text' },
      { key: 'langfuse_secret_key', label: 'Secret Key', type: 'password' },
    ],
  },
  {
    name: 'thehive',
    displayName: 'TheHive',
    fields: [
      { key: 'thehive_url', label: 'URL', type: 'text' },
      { key: 'thehive_api_key', label: 'API Key', type: 'password' },
    ],
  },
];

const SOURCE_CONTROL: ConnectionDef[] = [
  {
    name: 'github',
    displayName: 'GitHub',
    fields: [
      { key: 'github_token', label: 'Token', type: 'password' },
      { key: 'github_owner', label: 'Owner', type: 'text' },
      { key: 'github_repo', label: 'Repository', type: 'text' },
      { key: 'github_base_branch', label: 'Base Branch', type: 'text' },
    ],
  },
];

export function ConnectionsPage() {
  const queryClient = useQueryClient();
  const [testingConnection, setTestingConnection] = useState<string | null>(
    null,
  );

  const { data: connections = [], isLoading } = useQuery({
    queryKey: ['connections'],
    queryFn: () => api.getConnections(),
    select: (data) => data.connections,
    refetchInterval: 30_000,
  });

  const testMutation = useMutation({
    mutationFn: (name: string) => {
      setTestingConnection(name);
      return api.testConnection(name);
    },
    onSettled: () => {
      setTestingConnection(null);
      queryClient.invalidateQueries({ queryKey: ['connections'] });
    },
  });

  const connectionMap = useMemo(() => {
    const map: Record<string, ConnectionStatus> = {};
    for (const conn of connections) {
      map[conn.name] = conn;
    }
    return map;
  }, [connections]);

  const renderSection = (title: string, defs: ConnectionDef[]) => (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {defs.map((def) => {
          const conn = connectionMap[def.name];
          return (
            <ConnectionCard
              key={def.name}
              name={def.name}
              displayName={def.displayName}
              status={conn?.status ?? 'disconnected'}
              lastChecked={conn?.last_checked}
              fields={def.fields}
              onTest={() => testMutation.mutate(def.name)}
              testing={testingConnection === def.name}
            />
          );
        })}
      </div>
    </div>
  );

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <Cable className="h-7 w-7" />
        <h1 className="text-3xl font-bold tracking-tight">Connections</h1>
      </div>

      {renderSection('Notification Channels', NOTIFICATION_CHANNELS)}
      <Separator />
      {renderSection('Tool Backends', TOOL_BACKENDS)}
      <Separator />
      {renderSection('Source Control', SOURCE_CONTROL)}
    </div>
  );
}
