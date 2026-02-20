import { useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Card } from '@/components/ui/card';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { TimeAgo } from '@/components/shared/TimeAgo';

interface ServiceResult {
  service: string;
  healthy: boolean;
  errors: string[];
  warnings: string[];
  timestamp: string;
}

interface ServiceHealthGridProps {
  results: ServiceResult[];
  onServiceClick?: (service: string) => void;
}

function getServiceStatus(result: ServiceResult): 'healthy' | 'warning' | 'error' {
  if (!result.healthy || result.errors.length > 0) return 'error';
  if (result.warnings.length > 0) return 'warning';
  return 'healthy';
}

const statusDotColor = {
  healthy: 'bg-emerald-500',
  warning: 'bg-yellow-500',
  error: 'bg-red-500',
} as const;

export function ServiceHealthGrid({ results, onServiceClick }: ServiceHealthGridProps) {
  const [expanded, setExpanded] = useState<string | null>(null);

  const selectedResult = expanded
    ? results.find((r) => r.service === expanded)
    : null;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
        {results.map((result) => {
          const status = getServiceStatus(result);

          return (
            <button
              key={result.service}
              type="button"
              onClick={() => {
                setExpanded(expanded === result.service ? null : result.service);
                onServiceClick?.(result.service);
              }}
              className={cn(
                'flex items-center gap-2 rounded-md border border-border bg-card p-3 text-left transition-colors hover:bg-accent',
                expanded === result.service && 'ring-1 ring-ring',
              )}
            >
              <span
                className={cn(
                  'h-2.5 w-2.5 shrink-0 rounded-full',
                  statusDotColor[status],
                )}
              />
              <span className="truncate text-sm font-medium text-card-foreground">
                {result.service}
              </span>
            </button>
          );
        })}
      </div>

      {selectedResult && (
        <Card className="p-4">
          <div className="flex items-start justify-between">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold">{selectedResult.service}</h3>
                <StatusBadge status={getServiceStatus(selectedResult)} />
              </div>
              <p className="text-xs text-muted-foreground">
                Last checked: <TimeAgo date={selectedResult.timestamp} />
              </p>
            </div>
            <button
              type="button"
              onClick={() => setExpanded(null)}
              className="text-muted-foreground hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {selectedResult.errors.length > 0 && (
            <div className="mt-3 space-y-1">
              <p className="text-xs font-medium text-red-400">Errors</p>
              {selectedResult.errors.map((err, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2 rounded-sm bg-red-500/10 px-2 py-1.5 text-xs text-red-300"
                >
                  <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                  <span>{err}</span>
                </div>
              ))}
            </div>
          )}

          {selectedResult.warnings.length > 0 && (
            <div className="mt-3 space-y-1">
              <p className="text-xs font-medium text-yellow-400">Warnings</p>
              {selectedResult.warnings.map((warn, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2 rounded-sm bg-yellow-500/10 px-2 py-1.5 text-xs text-yellow-300"
                >
                  <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                  <span>{warn}</span>
                </div>
              ))}
            </div>
          )}

          {selectedResult.errors.length === 0 && selectedResult.warnings.length === 0 && (
            <p className="mt-3 text-xs text-muted-foreground">
              No issues detected.
            </p>
          )}
        </Card>
      )}
    </div>
  );
}
