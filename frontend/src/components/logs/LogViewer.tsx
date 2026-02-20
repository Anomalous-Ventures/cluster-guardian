import { useEffect, useRef, useState } from 'react';
import { Loader2, ArrowDownToLine } from 'lucide-react';
import { cn } from '@/lib/utils';

interface LogViewerProps {
  entries: Array<{
    timestamp: string;
    line: string;
    labels: Record<string, string>;
  }>;
  loading?: boolean;
}

const SEVERITY_PATTERNS: Array<{ pattern: RegExp; className: string }> = [
  { pattern: /\b(error|fatal|panic|exception)\b/i, className: 'text-red-400' },
  { pattern: /\b(warn|warning)\b/i, className: 'text-yellow-400' },
];

function getSeverityClass(line: string): string {
  for (const { pattern, className } of SEVERITY_PATTERNS) {
    if (pattern.test(line)) return className;
  }
  return 'text-foreground';
}

function formatLogTimestamp(timestamp: string): string {
  try {
    const d = new Date(timestamp);
    return d.toISOString().replace('T', ' ').replace('Z', '');
  } catch {
    return timestamp;
  }
}

export function LogViewer({ entries, loading }: LogViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const prevLengthRef = useRef(entries.length);

  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [entries, autoScroll]);

  useEffect(() => {
    if (entries.length !== prevLengthRef.current) {
      prevLengthRef.current = entries.length;
    }
  }, [entries.length]);

  function handleScroll() {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    const atBottom = scrollHeight - scrollTop - clientHeight < 40;
    setAutoScroll(atBottom);
  }

  return (
    <div className="relative flex flex-col rounded-md border border-border bg-card">
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="h-[500px] overflow-y-auto overflow-x-auto p-3 font-mono text-sm leading-relaxed"
      >
        {loading && entries.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            <span className="ml-2 text-muted-foreground">Loading logs...</span>
          </div>
        ) : entries.length === 0 ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            No log entries to display.
          </div>
        ) : (
          entries.map((entry, i) => (
            <div key={i} className="flex gap-3 whitespace-nowrap py-px hover:bg-accent/40">
              <span className="shrink-0 select-none text-muted-foreground">
                {formatLogTimestamp(entry.timestamp)}
              </span>
              <span className={cn('whitespace-pre-wrap break-all', getSeverityClass(entry.line))}>
                {entry.line}
              </span>
            </div>
          ))
        )}

        {loading && entries.length > 0 && (
          <div className="flex items-center gap-2 py-2 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="text-xs">Loading more...</span>
          </div>
        )}
      </div>

      {!autoScroll && (
        <button
          type="button"
          onClick={() => {
            setAutoScroll(true);
            if (containerRef.current) {
              containerRef.current.scrollTop = containerRef.current.scrollHeight;
            }
          }}
          className={cn(
            'absolute bottom-3 right-3 flex items-center gap-1.5 rounded-md',
            'bg-secondary px-3 py-1.5 text-xs text-secondary-foreground',
            'hover:bg-secondary/80 transition-colors shadow-lg',
          )}
        >
          <ArrowDownToLine className="h-3.5 w-3.5" />
          Scroll to bottom
        </button>
      )}
    </div>
  );
}
