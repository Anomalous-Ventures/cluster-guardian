import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

export function useHealthStatus() {
  return useQuery({
    queryKey: ['health'],
    queryFn: api.getHealth,
    refetchInterval: 10_000,
  });
}

export function useHealthChecks() {
  return useQuery({
    queryKey: ['health-checks'],
    queryFn: api.getHealthChecks,
    enabled: false,
  });
}

export function useServiceCheck(service: string) {
  return useQuery({
    queryKey: ['health-check', service],
    queryFn: () => api.checkService(service),
    enabled: false,
  });
}
