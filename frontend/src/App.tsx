import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { Shell } from '@/components/layout/Shell';
import { DashboardPage } from '@/pages/DashboardPage';
import { ScansPage } from '@/pages/ScansPage';
import { ServicesPage } from '@/pages/ServicesPage';
import { LogsPage } from '@/pages/LogsPage';
import { AuditPage } from '@/pages/AuditPage';
import { ConfigPage } from '@/pages/ConfigPage';
import { ConnectionsPage } from '@/pages/ConnectionsPage';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30000,
    },
  },
});

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Router>
        <Shell>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/scans" element={<ScansPage />} />
            <Route path="/services" element={<ServicesPage />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route path="/audit" element={<AuditPage />} />
            <Route path="/config" element={<ConfigPage />} />
            <Route path="/connections" element={<ConnectionsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Shell>
      </Router>
    </QueryClientProvider>
  );
}

export default App;
