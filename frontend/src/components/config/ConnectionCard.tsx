import { useState, useCallback } from 'react';
import { Loader2, ChevronDown, ChevronUp } from 'lucide-react';
import { cn } from '@/lib/utils';
import { formatRelativeTime } from '@/lib/utils';

interface ConnectionFieldDef {
  key: string;
  label: string;
  type: 'text' | 'password' | 'toggle';
  value?: any;
}

interface ConnectionCardProps {
  name: string;
  displayName: string;
  status: 'connected' | 'disconnected' | 'error' | 'unknown';
  lastChecked?: string;
  fields?: ConnectionFieldDef[];
  onTest: () => void;
  onSave?: (values: Record<string, any>) => void;
  testing?: boolean;
}

const STATUS_MAP: Record<
  ConnectionCardProps['status'],
  { label: string; dotClass: string; badgeClass: string }
> = {
  connected: {
    label: 'Connected',
    dotClass: 'bg-green-500',
    badgeClass: 'bg-green-500/10 text-green-400 border-green-500/20',
  },
  disconnected: {
    label: 'Disconnected',
    dotClass: 'bg-zinc-500',
    badgeClass: 'bg-zinc-500/10 text-zinc-400 border-zinc-500/20',
  },
  error: {
    label: 'Error',
    dotClass: 'bg-red-500',
    badgeClass: 'bg-red-500/10 text-red-400 border-red-500/20',
  },
  unknown: {
    label: 'Unknown',
    dotClass: 'bg-yellow-500',
    badgeClass: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20',
  },
};

const inputClasses = cn(
  'h-9 w-full rounded-md border border-input bg-background px-3 text-sm',
  'text-foreground placeholder:text-muted-foreground',
  'focus:outline-none focus:ring-1 focus:ring-ring',
);

export function ConnectionCard({
  name: _name,
  displayName,
  status,
  lastChecked,
  fields,
  onTest,
  onSave,
  testing,
}: ConnectionCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [local, setLocal] = useState<Record<string, any>>(() => {
    const init: Record<string, any> = {};
    fields?.forEach((f) => {
      init[f.key] = f.value;
    });
    return init;
  });

  const set = useCallback((key: string, val: any) => {
    setLocal((prev) => ({ ...prev, [key]: val }));
  }, []);

  const statusInfo = STATUS_MAP[status];
  const hasFields = fields && fields.length > 0;

  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between p-4">
        <div className="flex items-center gap-3">
          <div className="space-y-1">
            <h4 className="text-sm font-medium text-foreground">{displayName}</h4>
            {lastChecked && (
              <p className="text-xs text-muted-foreground">
                Checked {formatRelativeTime(lastChecked)}
              </p>
            )}
          </div>
        </div>

        <div className="flex items-center gap-3">
          <span
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium',
              statusInfo.badgeClass,
            )}
          >
            <span className={cn('h-1.5 w-1.5 rounded-full', statusInfo.dotClass)} />
            {statusInfo.label}
          </span>

          <button
            type="button"
            onClick={onTest}
            disabled={testing}
            className={cn(
              'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium',
              'border border-input bg-background text-foreground',
              'hover:bg-accent transition-colors',
              'disabled:opacity-50 disabled:pointer-events-none',
            )}
          >
            {testing && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Test Connection
          </button>

          {hasFields && (
            <button
              type="button"
              onClick={() => setExpanded(!expanded)}
              className={cn(
                'flex items-center justify-center rounded-md p-1.5',
                'text-muted-foreground hover:text-foreground hover:bg-accent transition-colors',
              )}
            >
              {expanded ? (
                <ChevronUp className="h-4 w-4" />
              ) : (
                <ChevronDown className="h-4 w-4" />
              )}
            </button>
          )}
        </div>
      </div>

      {expanded && hasFields && (
        <div className="border-t border-border p-4">
          <div className="space-y-3">
            {fields.map((field) => (
              <div key={field.key} className="space-y-1.5">
                <label
                  htmlFor={`conn-${field.key}`}
                  className="block text-sm font-medium text-foreground"
                >
                  {field.label}
                </label>

                {field.type === 'toggle' ? (
                  <button
                    id={`conn-${field.key}`}
                    type="button"
                    role="switch"
                    aria-checked={!!local[field.key]}
                    onClick={() => set(field.key, !local[field.key])}
                    className={cn(
                      'relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full',
                      'transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                      local[field.key] ? 'bg-primary' : 'bg-input',
                    )}
                  >
                    <span
                      className={cn(
                        'pointer-events-none block h-5 w-5 rounded-full bg-background shadow-lg ring-0',
                        'transition-transform mt-0.5',
                        local[field.key] ? 'translate-x-[22px]' : 'translate-x-0.5',
                      )}
                    />
                  </button>
                ) : (
                  <input
                    id={`conn-${field.key}`}
                    type={field.type}
                    value={local[field.key] ?? ''}
                    onChange={(e) => set(field.key, e.target.value)}
                    className={inputClasses}
                  />
                )}
              </div>
            ))}
          </div>

          {onSave && (
            <div className="mt-4 flex justify-end">
              <button
                type="button"
                onClick={() => onSave(local)}
                className={cn(
                  'flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium',
                  'bg-primary text-primary-foreground',
                  'hover:bg-primary/90 transition-colors',
                )}
              >
                Save
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
