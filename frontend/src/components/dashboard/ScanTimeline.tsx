import { useState, useMemo } from 'react';
import { CheckCircle2, XCircle, ChevronDown, ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { TimeAgo } from '@/components/shared/TimeAgo';

/**
 * Renders a markdown string as structured HTML elements.
 * Handles headings, bold, inline code, code blocks, lists, and paragraphs.
 */
function MarkdownContent({ content }: { content: string }) {
  const elements = useMemo(() => {
    const lines = content.split('\n');
    const result: { key: number; type: string; content: string }[] = [];
    let inCodeBlock = false;
    let codeBlockLines: string[] = [];

    lines.forEach((line, i) => {
      if (line.startsWith('```')) {
        if (inCodeBlock) {
          result.push({ key: i, type: 'code-block', content: codeBlockLines.join('\n') });
          codeBlockLines = [];
          inCodeBlock = false;
        } else {
          inCodeBlock = true;
        }
        return;
      }
      if (inCodeBlock) {
        codeBlockLines.push(line);
        return;
      }
      if (line.startsWith('### ')) {
        result.push({ key: i, type: 'h3', content: line.slice(4) });
      } else if (line.startsWith('## ')) {
        result.push({ key: i, type: 'h2', content: line.slice(3) });
      } else if (line.startsWith('# ')) {
        result.push({ key: i, type: 'h1', content: line.slice(2) });
      } else if (line.startsWith('- ') || line.startsWith('* ')) {
        result.push({ key: i, type: 'li', content: line.slice(2) });
      } else if (/^\d+\.\s/.test(line)) {
        result.push({ key: i, type: 'li', content: line.replace(/^\d+\.\s/, '') });
      } else if (line.trim() === '') {
        result.push({ key: i, type: 'br', content: '' });
      } else {
        result.push({ key: i, type: 'p', content: line });
      }
    });

    if (inCodeBlock && codeBlockLines.length > 0) {
      result.push({ key: lines.length, type: 'code-block', content: codeBlockLines.join('\n') });
    }

    return result;
  }, [content]);

  return (
    <div className="mt-1 space-y-1 text-sm text-foreground">
      {elements.map((el) => {
        switch (el.type) {
          case 'h1':
            return <p key={el.key} className="text-base font-bold">{formatInline(el.content)}</p>;
          case 'h2':
            return <p key={el.key} className="text-sm font-bold mt-2">{formatInline(el.content)}</p>;
          case 'h3':
            return <p key={el.key} className="text-sm font-semibold mt-1.5">{formatInline(el.content)}</p>;
          case 'li':
            return (
              <div key={el.key} className="flex gap-1.5 pl-2">
                <span className="text-muted-foreground shrink-0">-</span>
                <span>{formatInline(el.content)}</span>
              </div>
            );
          case 'code-block':
            return (
              <pre key={el.key} className="rounded-sm bg-muted/70 px-2 py-1.5 text-xs font-mono whitespace-pre-wrap overflow-x-auto">
                {el.content}
              </pre>
            );
          case 'br':
            return <div key={el.key} className="h-1" />;
          default:
            return <p key={el.key}>{formatInline(el.content)}</p>;
        }
      })}
    </div>
  );
}

/** Format inline markdown: **bold**, `code` */
function formatInline(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  let remaining = text;
  let idx = 0;

  while (remaining.length > 0) {
    // Bold: **text**
    const boldMatch = remaining.match(/\*\*(.+?)\*\*/);
    // Code: `text`
    const codeMatch = remaining.match(/`([^`]+)`/);

    const boldIdx = boldMatch?.index ?? Infinity;
    const codeIdx = codeMatch?.index ?? Infinity;

    if (boldIdx === Infinity && codeIdx === Infinity) {
      parts.push(remaining);
      break;
    }

    if (boldIdx <= codeIdx && boldMatch) {
      if (boldIdx > 0) parts.push(remaining.slice(0, boldIdx));
      parts.push(<strong key={idx++} className="font-semibold">{boldMatch[1]}</strong>);
      remaining = remaining.slice(boldIdx + boldMatch[0].length);
    } else if (codeMatch) {
      if (codeIdx > 0) parts.push(remaining.slice(0, codeIdx));
      parts.push(
        <code key={idx++} className="rounded bg-muted px-1 py-0.5 text-xs font-mono">
          {codeMatch[1]}
        </code>
      );
      remaining = remaining.slice(codeIdx + codeMatch[0].length);
    }
  }

  return parts.length === 1 ? parts[0] : <>{parts}</>;
}

interface ScanEntry {
  success: boolean;
  summary: string;
  timestamp: string;
  audit_log: any[];
}

interface ScanTimelineProps {
  scans: ScanEntry[];
}

function AuditLogDetail({ entries }: { entries: any[] }) {
  if (entries.length === 0) {
    return (
      <p className="py-1 text-xs text-muted-foreground">No audit log entries.</p>
    );
  }

  return (
    <div className="space-y-1.5">
      {entries.map((entry, i) => (
        <div
          key={i}
          className="rounded-sm border border-border bg-muted/50 px-3 py-2 text-xs"
        >
          {entry.action && (
            <span className="font-medium text-foreground">{entry.action}</span>
          )}
          {entry.resource && (
            <span className="ml-2 text-muted-foreground">{entry.resource}</span>
          )}
          {entry.message && (
            <p className="mt-0.5 text-muted-foreground">{entry.message}</p>
          )}
          {!entry.action && !entry.resource && !entry.message && (
            <pre className="whitespace-pre-wrap text-muted-foreground">
              {JSON.stringify(entry, null, 2)}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}

function TimelineEntry({ scan }: { scan: ScanEntry }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="relative flex gap-4 pb-6 last:pb-0">
      {/* Vertical line */}
      <div className="absolute left-[11px] top-6 bottom-0 w-px bg-border last:hidden" />

      {/* Status icon */}
      <div className="relative z-10 shrink-0">
        {scan.success ? (
          <CheckCircle2 className="h-6 w-6 text-emerald-500" />
        ) : (
          <XCircle className="h-6 w-6 text-red-500" />
        )}
      </div>

      {/* Content */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Badge variant={scan.success ? 'default' : 'destructive'}>
            {scan.success ? 'Pass' : 'Fail'}
          </Badge>
          <span className="text-xs text-muted-foreground">
            <TimeAgo date={scan.timestamp} />
          </span>
        </div>

        <MarkdownContent content={scan.summary} />

        {scan.audit_log.length > 0 && (
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="mt-2 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            {expanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
            Audit log ({scan.audit_log.length} {scan.audit_log.length === 1 ? 'entry' : 'entries'})
          </button>
        )}

        {expanded && (
          <div className="mt-2">
            <AuditLogDetail entries={scan.audit_log} />
          </div>
        )}
      </div>
    </div>
  );
}

export function ScanTimeline({ scans }: ScanTimelineProps) {
  if (scans.length === 0) {
    return (
      <p className="py-4 text-center text-sm text-muted-foreground">
        No scan results yet.
      </p>
    );
  }

  return (
    <div className="space-y-0">
      {scans.map((scan, i) => (
        <TimelineEntry key={`${scan.timestamp}-${i}`} scan={scan} />
      ))}
    </div>
  );
}
