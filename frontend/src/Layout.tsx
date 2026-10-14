import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import {
  LayoutDashboard, Building2, Users, Globe, Mail, Activity,
  CalendarClock, BarChart3, Settings, LogOut, Rocket,
} from 'lucide-react';
import { useAuth } from './auth';

const nav = [
  { to: '/', icon: LayoutDashboard, label: 'Overview' },
  { to: '/companies', icon: Building2, label: 'Companies' },
  { to: '/leads', icon: Users, label: 'Leads' },
  { to: '/crawling', icon: Globe, label: 'Website Crawling' },
  { to: '/campaigns', icon: Mail, label: 'Email Campaigns' },
  { to: '/activity', icon: Activity, label: 'Email Activity' },
  { to: '/followups', icon: CalendarClock, label: 'Follow-ups' },
  { to: '/analytics', icon: BarChart3, label: 'Analytics' },
  { to: '/settings', icon: Settings, label: 'Settings' },
];

export default function Layout() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="flex min-h-screen bg-gray-50">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-gray-200 bg-white md:flex">
        <div className="flex h-16 items-center gap-2 border-b border-gray-200 px-5">
          <Rocket className="h-6 w-6 text-primary-600" />
          <span className="text-lg font-bold text-gray-900">LeadPilot Trial</span>
        </div>
        <nav className="flex-1 space-y-1 p-3">
          {nav.map((i) => (
            <NavLink
              key={i.to}
              to={i.to}
              end={i.to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium ${
                  isActive ? 'bg-primary-50 text-primary-700' : 'text-gray-600 hover:bg-gray-50'
                }`
              }
            >
              <i.icon className="h-5 w-5" />
              {i.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-gray-200 p-3">
          <p className="truncate px-2 text-xs text-gray-500">{user?.email}</p>
          <button
            onClick={() => signOut().then(() => navigate('/login'))}
            className="mt-1 flex w-full items-center gap-2 rounded-lg px-2 py-2 text-sm text-gray-600 hover:bg-gray-50"
          >
            <LogOut className="h-4 w-4" /> Sign out
          </button>
        </div>
      </aside>
      <div className="flex-1">
        <div className="sticky top-0 z-10 flex items-center gap-2 overflow-x-auto border-b border-gray-200 bg-white px-4 py-2 md:hidden">
          {nav.map((i) => (
            <NavLink key={i.to} to={i.to} end={i.to === '/'} className="whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50">
              {i.label}
            </NavLink>
          ))}
        </div>
        <main className="p-4 md:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
