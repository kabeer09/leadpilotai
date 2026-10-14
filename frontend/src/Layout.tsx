import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import {
  LayoutDashboard, Building2, Users, Globe, Mail, Activity,
  CalendarClock, BarChart3, Settings, LogOut, Zap, Search, Plus, Download,
} from 'lucide-react';
import { useState } from 'react';
import { useAuth } from './auth';

const menu = [
  { to: '/', icon: LayoutDashboard, label: 'Overview' },
  { to: '/companies', icon: Building2, label: 'Companies' },
  { to: '/leads', icon: Users, label: 'Leads' },
  { to: '/crawling', icon: Globe, label: 'Website Crawling' },
  { to: '/campaigns', icon: Mail, label: 'Email Campaigns' },
];
const other = [
  { to: '/activity', icon: Activity, label: 'Email Activity' },
  { to: '/followups', icon: CalendarClock, label: 'Follow-ups' },
  { to: '/analytics', icon: BarChart3, label: 'Analytics' },
  { to: '/settings', icon: Settings, label: 'Settings' },
];

function SideLink({ to, icon: Icon, label }: { to: string; icon: typeof Mail; label: string }) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        `flex items-center gap-3 rounded-xl px-3 py-2 text-[13px] font-medium transition-colors ${
          isActive ? 'bg-white/10 text-white' : 'text-white/55 hover:bg-white/5 hover:text-white'
        }`
      }
    >
      <Icon className="h-[18px] w-[18px]" />
      {label}
    </NavLink>
  );
}

export default function Layout() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const [q, setQ] = useState('');

  return (
    <div className="flex min-h-screen bg-[#f4f5f7]">
      {/* Dark sidebar */}
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col bg-[#0c0f16] p-4 md:flex">
        <div className="flex items-center gap-2 px-2 py-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary-600">
            <Zap className="h-4 w-4 text-white" />
          </span>
          <span className="text-[15px] font-bold text-white">LeadPilot</span>
          <span className="ml-auto rounded-md bg-white/10 px-1.5 py-0.5 text-[10px] text-white/60">trial</span>
        </div>
        <p className="px-2 pb-1 pt-4 text-[10px] font-semibold uppercase tracking-wider text-white/35">Menu</p>
        <nav className="space-y-0.5">
          {menu.map((i) => <SideLink key={i.to} {...i} />)}
        </nav>
        <p className="px-2 pb-1 pt-4 text-[10px] font-semibold uppercase tracking-wider text-white/35">Other</p>
        <nav className="space-y-0.5">
          {other.map((i) => <SideLink key={i.to} {...i} />)}
        </nav>
        <div className="mt-auto space-y-3">
          <div className="rounded-2xl bg-white/[0.06] p-3">
            <p className="text-xs font-semibold text-white">Trial workspace</p>
            <p className="mt-0.5 text-[11px] leading-snug text-white/50">Dry-run email mode. Connect a provider to go live.</p>
            <button onClick={() => navigate('/settings')} className="mt-2 w-full rounded-xl bg-primary-600 py-1.5 text-xs font-semibold text-white hover:bg-primary-500">
              Provider setup
            </button>
          </div>
          <div className="flex items-center gap-2 px-1">
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-white/15 text-xs font-bold text-white">
              {(user?.email || '?').slice(0, 1).toUpperCase()}
            </span>
            <p className="min-w-0 flex-1 truncate text-xs text-white/70">{user?.email}</p>
            <button onClick={() => signOut().then(() => navigate('/login'))} title="Sign out" className="text-white/50 hover:text-white">
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      {/* Content */}
      <div className="min-w-0 flex-1">
        {/* Topbar */}
        <header className="sticky top-0 z-10 border-b border-gray-200/70 bg-[#f4f5f7]/90 backdrop-blur">
          <div className="flex items-center gap-2 px-4 py-3 md:px-6">
            <form
              className="relative w-full max-w-md"
              onSubmit={(e) => {
                e.preventDefault();
                if (q.trim()) navigate(`/companies?q=${encodeURIComponent(q.trim())}`);
              }}
            >
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search companies…"
                className="w-full rounded-xl border border-gray-200 bg-white py-2 pl-9 pr-8 text-sm shadow-sm focus:border-primary-500 focus:outline-none"
              />
              <kbd className="absolute right-3 top-1/2 -translate-y-1/2 rounded bg-gray-100 px-1.5 text-[10px] text-gray-400">⏎</kbd>
            </form>
            <div className="ml-auto flex items-center gap-2">
              <button onClick={() => navigate('/campaigns')} className="hidden items-center gap-1 rounded-xl border border-gray-200 bg-white px-3 py-2 text-xs font-semibold text-gray-700 shadow-sm hover:bg-gray-50 sm:inline-flex">
                <Plus className="h-3.5 w-3.5" /> New
              </button>
              <a href="/api/companies/export" className="inline-flex items-center gap-1 rounded-xl bg-lime-400 px-3 py-2 text-xs font-semibold text-gray-900 shadow-sm hover:bg-lime-300">
                <Download className="h-3.5 w-3.5" /> Export
              </a>
              <span className="hidden h-8 w-8 items-center justify-center rounded-full bg-primary-600 text-xs font-bold text-white sm:flex">
                {(user?.email || '?').slice(0, 1).toUpperCase()}
              </span>
            </div>
          </div>
          {/* Mobile nav */}
          <nav className="flex gap-1 overflow-x-auto px-4 pb-2 md:hidden">
            {[...menu, ...other].map((i) => (
              <NavLink key={i.to} to={i.to} end={i.to === '/'} className="whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium text-gray-600">
                {i.label}
              </NavLink>
            ))}
          </nav>
        </header>
        <main className="p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
