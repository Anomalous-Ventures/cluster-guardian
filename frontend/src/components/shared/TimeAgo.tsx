import { useEffect, useState } from 'react';
import { formatDistanceToNow } from 'date-fns';
import { cn } from '@/lib/utils';

interface TimeAgoProps {
  date: Date | string | number;
  className?: string;
  refreshInterval?: number;
}

export function TimeAgo({
  date,
  className,
  refreshInterval = 30000,
}: TimeAgoProps) {
  const [, setTick] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => setTick((t) => t + 1), refreshInterval);
    return () => clearInterval(interval);
  }, [refreshInterval]);

  const parsed = date instanceof Date ? date : new Date(date);
  const relative = formatDistanceToNow(parsed, { addSuffix: true });

  return (
    <time
      dateTime={parsed.toISOString()}
      title={parsed.toLocaleString()}
      className={cn('text-sm text-muted-foreground', className)}
    >
      {relative}
    </time>
  );
}
