import axios from 'axios';

const client = axios.create({ baseURL: '', headers: { 'Content-Type': 'application/json' } });

// Types
export interface HealthResponse { status: string; version: string; timestamp: string; components: Record<string, string>; }
export interface ScanResponse { success: boolean; summary: string; audit_log: AuditEntry[]; rate_limit: Record<string, number>; timestamp: string; }
export interface AuditEntry { timestamp: string; action: string; target?: string; namespace?: string; reason?: string; result?: string; }
export interface HealthCheckResult { service: string; healthy: boolean; checks: any[]; errors: string[]; warnings: string[]; timestamp: string; }
export interface LogEntry { timestamp: string; line: string; labels: Record<string, string>; }
export interface LogLabels { namespaces: string[]; pods: string[]; containers: string[]; }
export interface ConnectionStatus { name: string; status: 'connected' | 'disconnected' | 'error'; last_checked: string; }
export interface Approval { id: string; action: string; description: string; namespace: string; timestamp: string; status: 'pending' | 'approved' | 'rejected'; }
export interface K8sEvent { timestamp: string; type: string; reason: string; message: string; object: string; namespace: string; }

export const api = {
  // Health
  getHealth: async () => (await client.get<HealthResponse>('/health')).data,

  // Scans
  triggerScan: async () => (await client.post<ScanResponse>('/api/v1/scan')).data,
  getLastScan: async () => (await client.get<ScanResponse>('/api/v1/scan/last')).data,
  investigate: async (description: string, thread_id?: string) => (await client.post('/api/v1/investigate', { description, thread_id })).data,

  // Health checks
  getHealthChecks: async () => (await client.get<{ results: HealthCheckResult[]; healthy: number; unhealthy: number }>('/api/v1/health-checks')).data,
  checkService: async (service: string) => (await client.get<HealthCheckResult>(`/api/v1/health-checks/${service}`)).data,

  // Audit log
  getAuditLog: async () => (await client.get<{ entries: AuditEntry[]; rate_limit: Record<string, number> }>('/api/v1/audit-log')).data,

  // CrashLoop pods
  getCrashLoopPods: async () => (await client.get('/api/v1/crashloopbackoff')).data,

  // Status page
  getStatusPage: async () => (await client.get<{ endpoints: { name: string; group: string; healthy: boolean; hostname: string; last_check: string; uptime_7d: number }[] }>('/api/v1/status-page')).data,

  // Logs
  getLogs: async (params: { query?: string; namespace?: string; pod?: string; container?: string; severity?: string; since?: string; limit?: number }) => (await client.get<{ entries: LogEntry[]; total: number }>('/api/v1/logs', { params })).data,
  getLogLabels: async () => (await client.get<LogLabels>('/api/v1/logs/labels')).data,
  getEvents: async (namespace?: string) => (await client.get<{ events: K8sEvent[] }>('/api/v1/events', { params: { namespace } })).data,

  // Config
  getConfig: async () => (await client.get<Record<string, any>>('/api/v1/config')).data,
  updateConfig: async (updates: Record<string, any>) => (await client.patch('/api/v1/config', updates)).data,
  resetConfig: async (key: string) => (await client.post('/api/v1/config/reset', { key })).data,

  // Connections
  getConnections: async () => (await client.get<{ connections: ConnectionStatus[] }>('/api/v1/connections')).data,
  testConnection: async (name: string) => (await client.post<{ name: string; success: boolean; message: string }>(`/api/v1/connections/${name}/test`)).data,

  // Approvals
  getApprovals: async () => (await client.get<{ approvals: Approval[] }>('/api/v1/approvals')).data,
  approveAction: async (id: string) => (await client.post(`/api/v1/approvals/${id}/approve`)).data,
  rejectAction: async (id: string) => (await client.post(`/api/v1/approvals/${id}/reject`)).data,
};

export default client;
