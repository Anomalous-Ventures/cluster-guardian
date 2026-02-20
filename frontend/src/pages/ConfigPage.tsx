import { useState } from 'react';
import { Loader2, Settings } from 'lucide-react';

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

import { ConfigForm } from '@/components/config/ConfigForm';
import type { ConfigField } from '@/components/config/ConfigForm';

import { useConfig, useUpdateConfig } from '@/hooks/useConfig';

const GENERAL_FIELDS: ConfigField[] = [
  {
    key: 'autonomy_level',
    label: 'Autonomy Level',
    type: 'select',
    options: ['manual', 'conditional', 'auto'],
    description: 'Controls how much autonomous action the guardian can take.',
  },
  {
    key: 'scan_interval_seconds',
    label: 'Scan Interval (seconds)',
    type: 'number',
    description: 'How often the guardian runs automatic scans.',
  },
  {
    key: 'max_agent_iterations',
    label: 'Max Agent Iterations',
    type: 'number',
    description: 'Maximum number of reasoning steps per investigation.',
  },
  {
    key: 'log_level',
    label: 'Log Level',
    type: 'select',
    options: ['debug', 'info', 'warning', 'error'],
    description: 'Verbosity of guardian logging output.',
  },
  {
    key: 'debug',
    label: 'Debug Mode',
    type: 'toggle',
    description: 'Enable detailed debug output for troubleshooting.',
  },
];

const LLM_FIELDS: ConfigField[] = [
  {
    key: 'llm_model',
    label: 'LLM Model',
    type: 'text',
    description: 'The language model used for reasoning and analysis.',
  },
  {
    key: 'llm_base_url',
    label: 'LLM Base URL',
    type: 'text',
    description: 'API endpoint for the LLM backend.',
  },
  {
    key: 'llm_api_key',
    label: 'LLM API Key',
    type: 'password',
    description: 'Authentication key for the LLM service.',
  },
  {
    key: 'embedding_model',
    label: 'Embedding Model',
    type: 'text',
    description: 'Model used for vector embeddings and similarity search.',
  },
];

const SAFETY_FIELDS: ConfigField[] = [
  {
    key: 'max_actions_per_hour',
    label: 'Max Actions Per Hour',
    type: 'number',
    description: 'Rate limit on autonomous actions to prevent runaway behavior.',
  },
  {
    key: 'protected_namespaces',
    label: 'Protected Namespaces',
    type: 'tags',
    description: 'Namespaces where destructive actions are blocked.',
  },
  {
    key: 'require_approval_actions',
    label: 'Require Approval Actions',
    type: 'tags',
    description: 'Action types that require manual approval before execution.',
  },
  {
    key: 'quiet_hours_start',
    label: 'Quiet Hours Start',
    type: 'text',
    description: 'Start of quiet hours window (HH:MM format).',
  },
  {
    key: 'quiet_hours_end',
    label: 'Quiet Hours End',
    type: 'text',
    description: 'End of quiet hours window (HH:MM format).',
  },
  {
    key: 'dry_run_mode',
    label: 'Dry Run Mode',
    type: 'toggle',
    description:
      'When enabled, actions are simulated but not executed on the cluster.',
  },
];

const NOTIFICATION_FIELDS: ConfigField[] = [
  {
    key: 'notification_rate_limit',
    label: 'Notification Rate Limit',
    type: 'number',
    description: 'Maximum number of notifications sent per hour.',
  },
  {
    key: 'default_severity_threshold',
    label: 'Default Severity Threshold',
    type: 'select',
    options: ['info', 'warning', 'critical'],
    description:
      'Minimum severity level required to trigger a notification.',
  },
];

const HEALTH_CHECK_FIELDS: ConfigField[] = [
  {
    key: 'health_check_interval',
    label: 'Health Check Interval (seconds)',
    type: 'number',
    description: 'How often service health checks are performed.',
  },
  {
    key: 'ssl_warning_days',
    label: 'SSL Warning Days',
    type: 'number',
    description:
      'Days before SSL certificate expiry to trigger a warning.',
  },
];

export function ConfigPage() {
  const [activeTab, setActiveTab] = useState('general');
  const { data: config, isLoading } = useConfig();
  const { mutate: updateConfig, isPending: saving } = useUpdateConfig();

  const handleSave = (values: Record<string, any>) => {
    updateConfig(values);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const configValues = (config ?? {}) as Record<string, any>;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <Settings className="h-7 w-7" />
        <h1 className="text-3xl font-bold tracking-tight">Configuration</h1>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="llm">LLM Backend</TabsTrigger>
          <TabsTrigger value="safety">Safety Controls</TabsTrigger>
          <TabsTrigger value="notifications">Notifications</TabsTrigger>
          <TabsTrigger value="health">Health Checks</TabsTrigger>
        </TabsList>

        <TabsContent value="general">
          <ConfigForm
            title="General Settings"
            fields={GENERAL_FIELDS}
            values={configValues}
            onSave={handleSave}
            saving={saving}
          />
        </TabsContent>

        <TabsContent value="llm">
          <ConfigForm
            title="LLM Backend"
            fields={LLM_FIELDS}
            values={configValues}
            onSave={handleSave}
            saving={saving}
          />
        </TabsContent>

        <TabsContent value="safety">
          <ConfigForm
            title="Safety Controls"
            fields={SAFETY_FIELDS}
            values={configValues}
            onSave={handleSave}
            saving={saving}
          />
        </TabsContent>

        <TabsContent value="notifications">
          <ConfigForm
            title="Notification Settings"
            fields={NOTIFICATION_FIELDS}
            values={configValues}
            onSave={handleSave}
            saving={saving}
          />
        </TabsContent>

        <TabsContent value="health">
          <ConfigForm
            title="Health Check Settings"
            fields={HEALTH_CHECK_FIELDS}
            values={configValues}
            onSave={handleSave}
            saving={saving}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
