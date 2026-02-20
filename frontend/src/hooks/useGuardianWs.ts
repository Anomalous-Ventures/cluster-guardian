import { useEffect, useState, useCallback, useRef } from 'react';
import { useWebSocket } from '@/lib/ws';
import { useQueryClient } from '@tanstack/react-query';

interface WsMessage {
  type: string;
  [key: string]: unknown;
}

interface GuardianEvent {
  id: string;
  type: string;
  timestamp: string;
  data: Record<string, unknown>;
}

const MAX_EVENTS = 50;

function buildWsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/ws`;
}

export function useGuardianWs() {
  const queryClient = useQueryClient();
  const { connected, lastMessage, send } = useWebSocket(buildWsUrl());
  const [events, setEvents] = useState<GuardianEvent[]>([]);
  const eventIdCounter = useRef(0);

  const handleMessage = useCallback(
    (message: WsMessage) => {
      const event: GuardianEvent = {
        id: `evt-${Date.now()}-${eventIdCounter.current++}`,
        type: message.type,
        timestamp: (message.timestamp as string) ?? new Date().toISOString(),
        data: message as Record<string, unknown>,
      };

      setEvents((prev) => [event, ...prev].slice(0, MAX_EVENTS));

      switch (message.type) {
        case 'scan_complete':
          queryClient.invalidateQueries({ queryKey: ['lastScan'] });
          queryClient.invalidateQueries({ queryKey: ['auditLog'] });
          break;
        case 'health_update':
          queryClient.invalidateQueries({ queryKey: ['healthStatus'] });
          queryClient.invalidateQueries({ queryKey: ['healthChecks'] });
          break;
        case 'alert_received':
        case 'security_alert':
          queryClient.invalidateQueries({ queryKey: ['auditLog'] });
          break;
      }
    },
    [queryClient],
  );

  useEffect(() => {
    if (lastMessage) {
      handleMessage(lastMessage as WsMessage);
    }
  }, [lastMessage, handleMessage]);

  return { connected, lastMessage, send, events };
}
