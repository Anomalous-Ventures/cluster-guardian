import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle,
  Loader2,
  MessageSquare,
  Play,
  ScanSearch,
  ShieldCheck,
  XCircle,
} from 'lucide-react';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';

import { ScanTimeline } from '@/components/dashboard/ScanTimeline';
import { TimeAgo } from '@/components/shared/TimeAgo';
import { StatusBadge } from '@/components/shared/StatusBadge';

import { api } from '@/lib/api';

export function ScansPage() {
  const queryClient = useQueryClient();
  const [investigateText, setInvestigateText] = useState('');

  const { data: lastScan } = useQuery({
    queryKey: ['lastScan'],
    queryFn: () => api.getLastScan(),
    refetchInterval: 15_000,
  });

  const { data: approvals = [] } = useQuery({
    queryKey: ['approvals'],
    queryFn: () => api.getApprovals(),
    select: (data) => data.approvals,
    refetchInterval: 10_000,
  });

  const scanMutation = useMutation({
    mutationFn: () => api.triggerScan(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['lastScan'] });
      queryClient.invalidateQueries({ queryKey: ['auditLog'] });
    },
  });

  const investigateMutation = useMutation({
    mutationFn: (description: string) => api.investigate(description),
    onSuccess: () => {
      setInvestigateText('');
      queryClient.invalidateQueries({ queryKey: ['lastScan'] });
    },
  });

  const approveMutation = useMutation({
    mutationFn: (id: string) => api.approveAction(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['approvals'] }),
  });

  const rejectMutation = useMutation({
    mutationFn: (id: string) => api.rejectAction(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['approvals'] }),
  });

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

  const pendingApprovals = approvals.filter((a) => a.status === 'pending');

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold tracking-tight">Scans</h1>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ScanSearch className="h-5 w-5" />
              Trigger Scan
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground mb-4">
              Run a full cluster scan to detect issues, misconfigurations, and
              security concerns.
            </p>
            <Button
              onClick={() => scanMutation.mutate()}
              disabled={scanMutation.isPending}
            >
              {scanMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Play className="mr-2 h-4 w-4" />
              )}
              {scanMutation.isPending ? 'Scanning...' : 'Trigger Scan'}
            </Button>
            {scanMutation.isSuccess && (
              <p className="text-sm text-green-600 mt-2">
                Scan completed successfully.
              </p>
            )}
            {scanMutation.isError && (
              <p className="text-sm text-destructive mt-2">
                Scan failed:{' '}
                {(scanMutation.error as Error)?.message ?? 'Unknown error'}
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <MessageSquare className="h-5 w-5" />
              Investigate Issue
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground mb-4">
              Describe an issue in natural language and the guardian agent will
              investigate it.
            </p>
            <textarea
              className="w-full min-h-[100px] rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              placeholder="e.g. Pods in the payments namespace are restarting frequently..."
              value={investigateText}
              onChange={(e) => setInvestigateText(e.target.value)}
            />
            <Button
              className="mt-3"
              onClick={() => investigateMutation.mutate(investigateText)}
              disabled={
                investigateMutation.isPending || investigateText.trim() === ''
              }
            >
              {investigateMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <ScanSearch className="mr-2 h-4 w-4" />
              )}
              {investigateMutation.isPending
                ? 'Investigating...'
                : 'Investigate'}
            </Button>
            {investigateMutation.isSuccess && (
              <div className="mt-3 rounded-md bg-muted p-3 text-sm">
                <p className="font-medium mb-1">Result:</p>
                <p>
                  {(investigateMutation.data as any)?.summary ??
                    JSON.stringify(investigateMutation.data)}
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Separator />

      <Card>
        <CardHeader>
          <CardTitle>Scan Results</CardTitle>
        </CardHeader>
        <CardContent>
          {scanEntries.length > 0 ? (
            <ScanTimeline scans={scanEntries} />
          ) : (
            <p className="text-sm text-muted-foreground">
              No scan results available. Trigger a scan to get started.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5" />
            Pending Approvals
            {pendingApprovals.length > 0 && (
              <Badge variant="destructive">{pendingApprovals.length}</Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {pendingApprovals.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No actions awaiting approval.
            </p>
          ) : (
            <div className="space-y-3">
              {pendingApprovals.map((approval) => (
                <div
                  key={approval.id}
                  className="flex items-start justify-between gap-4 rounded-lg border p-4"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-medium text-sm">
                        {approval.action}
                      </span>
                      <StatusBadge
                        status={
                          approval.status === 'pending' ? 'warning' : 'healthy'
                        }
                        label={approval.status}
                      />
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {approval.description}
                    </p>
                    <div className="flex items-center gap-2 mt-1 text-xs text-muted-foreground">
                      <span>{approval.namespace}</span>
                      <span>--</span>
                      <TimeAgo date={approval.timestamp} />
                    </div>
                  </div>
                  <div className="flex gap-2 shrink-0">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => approveMutation.mutate(approval.id)}
                      disabled={approveMutation.isPending}
                    >
                      <CheckCircle className="mr-1 h-3.5 w-3.5" />
                      Approve
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => rejectMutation.mutate(approval.id)}
                      disabled={rejectMutation.isPending}
                    >
                      <XCircle className="mr-1 h-3.5 w-3.5" />
                      Reject
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
