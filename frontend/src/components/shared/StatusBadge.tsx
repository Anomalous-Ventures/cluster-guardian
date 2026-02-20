import { cn } from '@/lib/utils';

type Status =
  | 'healthy'
  | 'connected'
  | 'warning'
  | 'degraded'
  | 'unhealthy'
  | 'error'
  | 'disconnected'
  | 'unknown';

const statusConfig: Record<Status, { color: string; label: string }> = {
  healthy: { color: 'bg-emerald-500/15 text-emerald-400', label: 'Healthy' },
  connected: { color: 'bg-emerald-500/15 text-emerald-400', label: 'Connected' },
  warning: { color: 'bg-yellow-500/15 text-yellow-400', label: 'Warning' },
  degraded: { color: 'bg-yellow-500/15 text-yellow-400', label: 'Degraded' },
  unhealthy: { color: 'bg-red-500/15 text-red-400', label: 'Unhealthy' },
  error: { color: 'bg-red-500/15 text-red-400', label: 'Error' },
  disconnected: { color: 'bg-red-500/15 text-red-400', label: 'Disconnected' },
  unknown: { color: 'bg-zinc-500/15 text-zinc-400', label: 'Unknown' },
};

interface StatusBadgeProps {
  status: Status;
  label?: string;
  className?: string;
}

export function StatusBadge({ status, label, className }: StatusBadgeProps) {
  const config = statusConfig[status] ?? statusConfig.unknown;

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border border-transparent px-2.5 py-0.5 text-xs font-semibold',
        config.color,
        className,
      )}
    >
      <span
        className={cn('h-1.5 w-1.5 rounded-full', {
          'bg-emerald-400': status === 'healthy' || status === 'connected',
          'bg-yellow-400': status === 'warning' || status === 'degraded',
          'bg-red-400': status === 'unhealthy' || status === 'error' || status === 'disconnected',
          'bg-zinc-400': status === 'unknown',
        })}
      />
      {label ?? config.label}
    </span>
  );
}

export type { Status };
