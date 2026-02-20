import { Link, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  Search,
  Server,
  ScrollText,
  Shield,
  Settings,
  Cable,
  ExternalLink,
} from 'lucide-react';
import { cn } from '@/lib/utils';

const navItems = [
  { path: '/', label: 'Dashboard', icon: LayoutDashboard },
  { path: '/scans', label: 'Scans', icon: Search },
  { path: '/services', label: 'Services', icon: Server },
  { path: '/logs', label: 'Logs', icon: ScrollText },
  { path: '/audit', label: 'Audit', icon: Shield },
  { path: '/config', label: 'Configuration', icon: Settings },
  { path: '/connections', label: 'Connections', icon: Cable },
] as const;

const externalLinks = [
  { href: 'https://status.spooty.io', label: 'Status Page' },
  { href: 'https://headlamp.spooty.io', label: 'Headlamp' },
  { href: 'https://grafana.spooty.io', label: 'Grafana' },
] as const;

export function Sidebar() {
  const { pathname } = useLocation();

  return (
    <aside className="flex h-full w-60 flex-col border-r border-border bg-secondary">
      <div className="flex h-14 items-center border-b border-border px-4">
        <Shield className="mr-2 h-6 w-6 text-primary" />
        <span className="text-lg font-semibold text-foreground">
          Cluster Guardian
        </span>
      </div>

      <nav className="flex-1 space-y-1 p-3">
        {navItems.map(({ path, label, icon: Icon }) => {
          const isActive =
            path === '/' ? pathname === '/' : pathname.startsWith(path);

          return (
            <Link
              key={path}
              to={path}
              className={cn(
                'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
                isActive
                  ? 'bg-accent text-accent-foreground'
                  : 'text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground',
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </Link>
          );
        })}

        <div className="pt-4">
          <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            External
          </p>
          {externalLinks.map(({ href, label }) => (
            <a
              key={href}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent/50 hover:text-accent-foreground"
            >
              <ExternalLink className="h-4 w-4" />
              {label}
            </a>
          ))}
        </div>
      </nav>
    </aside>
  );
}
