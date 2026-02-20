import { useState, useCallback } from 'react';
import { Search } from 'lucide-react';
import { cn } from '@/lib/utils';

interface LogFilters {
  namespace?: string;
  pod?: string;
  container?: string;
  severity?: string;
  query?: string;
  since?: string;
}

interface LogFiltersProps {
  namespaces: string[];
  pods: string[];
  containers: string[];
  onFilterChange: (filters: LogFilters) => void;
}

const SEVERITY_OPTIONS = ['', 'info', 'warning', 'error', 'critical'] as const;

const TIME_RANGES = [
  { label: '5m', value: '5m' },
  { label: '15m', value: '15m' },
  { label: '30m', value: '30m' },
  { label: '1h', value: '1h' },
  { label: '6h', value: '6h' },
  { label: '24h', value: '24h' },
] as const;

const selectClasses = cn(
  'h-9 rounded-md border border-input bg-background px-3 text-sm',
  'text-foreground focus:outline-none focus:ring-1 focus:ring-ring',
);

const inputClasses = cn(
  'h-9 rounded-md border border-input bg-background px-3 pl-9 text-sm',
  'text-foreground placeholder:text-muted-foreground',
  'focus:outline-none focus:ring-1 focus:ring-ring',
);

export function LogFilters({ namespaces, pods, containers, onFilterChange }: LogFiltersProps) {
  const [filters, setFilters] = useState<LogFilters>({});

  const update = useCallback(
    (patch: Partial<LogFilters>) => {
      setFilters((prev) => {
        const next = { ...prev, ...patch };
        onFilterChange(next);
        return next;
      });
    },
    [onFilterChange],
  );

  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        value={filters.namespace ?? ''}
        onChange={(e) => update({ namespace: e.target.value || undefined })}
        className={selectClasses}
      >
        <option value="">All Namespaces</option>
        {namespaces.map((ns) => (
          <option key={ns} value={ns}>
            {ns}
          </option>
        ))}
      </select>

      <select
        value={filters.pod ?? ''}
        onChange={(e) => update({ pod: e.target.value || undefined })}
        className={selectClasses}
      >
        <option value="">All Pods</option>
        {pods.map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>

      <select
        value={filters.container ?? ''}
        onChange={(e) => update({ container: e.target.value || undefined })}
        className={selectClasses}
      >
        <option value="">All Containers</option>
        {containers.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>

      <select
        value={filters.severity ?? ''}
        onChange={(e) => update({ severity: e.target.value || undefined })}
        className={selectClasses}
      >
        <option value="">All Severities</option>
        {SEVERITY_OPTIONS.filter(Boolean).map((s) => (
          <option key={s} value={s}>
            {s.charAt(0).toUpperCase() + s.slice(1)}
          </option>
        ))}
      </select>

      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <input
          type="text"
          placeholder="Search logs..."
          value={filters.query ?? ''}
          onChange={(e) => update({ query: e.target.value || undefined })}
          className={inputClasses}
        />
      </div>

      <div className="flex items-center rounded-md border border-input">
        {TIME_RANGES.map(({ label, value }) => (
          <button
            key={value}
            type="button"
            onClick={() => update({ since: filters.since === value ? undefined : value })}
            className={cn(
              'h-9 px-3 text-sm transition-colors',
              'first:rounded-l-md last:rounded-r-md',
              filters.since === value
                ? 'bg-primary text-primary-foreground'
                : 'bg-background text-muted-foreground hover:text-foreground hover:bg-accent',
            )}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}
