import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from 'recharts';
import { Building2, Mail, Send, MessageSquare, ArrowUpRight, MoreHorizontal } from 'lucide-react';
import { overview, trends, listCompanies, listFollowups } from '../lib/api';
import { useAuth } from '../auth';

interface Ov { [k: string]: number }
interface Day { date: string; discovered: number; sent: number }

function deltaBadge(cur: number, prev: number) {
  if (prev <= 0) return cur > 0 ? <span className="rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold text-emerald-700">new</span> : null;
  const pct = Math.round(((cur - prev) / prev) * 100);
  if (pct === 0) return <span className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-bold text-gray-500">±0%</span>;
  const up = pct > 0;
  return (
    <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold ${up ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-600'}`}>
      {up ? '↑' : '↓'} {Math.abs(pct)}%
    </span>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const [data, setData] = useState<Ov | null>(null);
  const [daily, setDaily] = useState<Day[]>([]);
  const [recent, setRecent] = useState<{ id: number; name: string; emails: string[]; lead_status: string; updated_at?: string }[]>([]);
  const [due, setDue] = useState<{ id: number; lead_id: number; company: string | null; scheduled_at: string }[]>([]);
  const [activity, setActivity] = useState<{ type: string; message_id: number; at: string }[]>([]);

  useEffect(() => {
    overview(30).then((o) => {
      setData(o);
      setActivity(o.recent_activity || []);
    }).catch(() => {});
    trends(30).then((t) => setDaily(t.daily)).catch(() => {});
    listCompanies({ page: 1, page_size: 5 }).then((d) => setRecent(d.items)).catch(() => {});
    listFollowups().then((f) => setDue(f.filter((x: { status: string }) => x.status === 'SCHEDULED').slice(0, 5))).catch(() => {});
  }, []);

  const sum = (arr: Day[], k: 'discovered' | 'sent', from: number, to: number) =>
    arr.slice(from, to).reduce((a, d) => a + (d[k] || 0), 0);
  const n = daily.length;
  const disc7 = sum(daily, 'discovered', Math.max(0, n - 7), n);
  const discPrev = sum(daily, 'discovered', Math.max(0, n - 14), Math.max(0, n - 7));
  const sent7 = sum(daily, 'sent', Math.max(0, n - 7), n);
  const sentPrev = sum(daily, 'sent', Math.max(0, n - 14), Math.max(0, n - 7));

  const total = data?.companies ?? 0;
  const withEmail = data?.with_email ?? 0;
  const emailPct = total > 0 ? Math.round((withEmail / total) * 100) : 0;
  const donut = [
    { name: 'With email', value: withEmail },
    { name: 'No email yet', value: Math.max(0, total - withEmail) },
  ];

  const stats = [
    { label: 'Companies', value: total, sub: `${disc7.toLocaleString()} new · last 7 days`, badge: deltaBadge(disc7, discPrev), icon: Building2 },
    { label: 'With business email', value: withEmail, sub: total > 0 ? `${emailPct}% coverage` : 'crawl to discover', badge: null, icon: Mail },
    { label: 'Emails attempted', value: data?.emails_attempted ?? 0, sub: `${sent7.toLocaleString()} sent · last 7 days`, badge: deltaBadge(sent7, sentPrev), icon: Send },
    { label: 'Replies', value: data?.replies ?? 0, sub: `${data?.followups_due ?? 0} follow-ups due`, badge: null, icon: MessageSquare },
  ];

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold text-gray-900">Dashboard</h1>
        <p className="text-xs text-gray-500">Welcome{user?.name ? `, ${user.name}` : ''} — live numbers from your workspace.</p>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map((s) => (
          <div key={s.label} className="rounded-2xl border border-gray-200/70 bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between">
              <p className="text-xs font-medium text-gray-500">{s.label}</p>
              <ArrowUpRight className="h-3.5 w-3.5 text-gray-300" />
            </div>
            <div className="mt-1 flex items-center gap-2">
              <p className="text-2xl font-bold text-gray-900">{s.value.toLocaleString()}</p>
              {s.badge}
            </div>
            <p className="mt-0.5 text-[11px] text-gray-400">vs. previous 7 days period</p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        {/* Pipeline chart */}
        <div className="rounded-2xl border border-gray-200/70 bg-white p-4 shadow-sm xl:col-span-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-gray-900">Pipeline Performance</h2>
            <div className="flex items-center gap-2 text-[11px] text-gray-400">
              <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-primary-500" /> Discovered</span>
              <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-emerald-400" /> Sent</span>
            </div>
          </div>
          <div className="mt-2 h-60">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={daily}>
                <defs>
                  <linearGradient id="gD" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#2f6df6" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="#2f6df6" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="gS" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#34d399" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="#34d399" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#eef0f3" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} tickLine={false} axisLine={false} interval={Math.max(0, Math.floor(daily.length / 6) - 1)} />
                <YAxis tick={{ fontSize: 10 }} tickLine={false} axisLine={false} width={30} />
                <Tooltip />
                <Area type="monotone" dataKey="discovered" stroke="#2f6df6" fill="url(#gD)" strokeWidth={2} dot={false} />
                <Area type="monotone" dataKey="sent" stroke="#34d399" fill="url(#gS)" strokeWidth={2} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-1 flex items-center justify-between">
            <p className="text-[11px] text-gray-400">Last 30 days · from your crawl and send history</p>
            <Link to="/analytics" className="text-[11px] font-semibold text-primary-600">View all</Link>
          </div>
        </div>

        <div className="space-y-3">
          {/* Coverage donut */}
          <div className="rounded-2xl border border-gray-200/70 bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-bold text-gray-900">Email Coverage</h2>
              <MoreHorizontal className="h-4 w-4 text-gray-300" />
            </div>
            <div className="relative mx-auto h-40 w-40">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={donut} dataKey="value" nameKey="name" innerRadius={52} outerRadius={72} startAngle={90} endAngle={-270} strokeWidth={0}>
                    <Cell fill="#2f6df6" />
                    <Cell fill="#e8ebf0" />
                  </Pie>
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
              <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                <p className="text-2xl font-bold text-gray-900">{emailPct}%</p>
                <p className="text-[10px] text-gray-400">with email</p>
              </div>
            </div>
            <p className="text-center text-[11px] text-gray-400">{withEmail.toLocaleString()} of {total.toLocaleString()} companies</p>
          </div>

          {/* Follow-ups rail */}
          <div className="rounded-2xl bg-gradient-to-br from-primary-600 to-primary-700 p-4 text-white shadow-sm">
            <h2 className="text-sm font-bold">Follow-ups due</h2>
            {due.length === 0 ? (
              <p className="mt-2 text-xs text-white/70">Nothing due. Enable follow-ups on a campaign and they appear here.</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {due.map((f) => (
                  <li key={f.id}>
                    <Link to={`/leads/${f.lead_id}`} className="block rounded-xl bg-white/10 p-2 text-xs hover:bg-white/15">
                      <span className="font-semibold">{f.company || `Lead #${f.lead_id}`}</span>
                      <span className="block text-[11px] text-white/70">due {new Date(f.scheduled_at).toLocaleString()}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            <Link to="/followups" className="mt-3 block rounded-xl bg-white/15 p-2 text-center text-xs font-semibold hover:bg-white/20">
              Open follow-ups
            </Link>
          </div>
        </div>
      </div>

      {/* Recent companies + activity */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <div className="rounded-2xl border border-gray-200/70 bg-white p-4 shadow-sm xl:col-span-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-gray-900">Recent Companies</h2>
            <Link to="/companies" className="text-[11px] font-semibold text-primary-600">View all</Link>
          </div>
          <div className="mt-2 overflow-x-auto">
            <table className="min-w-full">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wide text-gray-400">
                  <th className="py-2 pr-4 font-medium">Name</th>
                  <th className="py-2 pr-4 font-medium">Emails</th>
                  <th className="py-2 pr-4 font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {recent.length === 0 && (
                  <tr><td colSpan={3} className="py-6 text-center text-xs text-gray-400">No companies yet — <Link to="/crawling" className="text-primary-600">crawl a website</Link> to start.</td></tr>
                )}
                {recent.map((c) => (
                  <tr key={c.id}>
                    <td className="py-2 pr-4 text-[13px] font-semibold text-gray-900">
                      <Link to={`/companies/${c.id}`} className="hover:text-primary-600">{c.name}</Link>
                    </td>
                    <td className="py-2 pr-4 text-xs text-gray-500">{c.emails.length > 0 ? c.emails.slice(0, 2).join(', ') : '—'}</td>
                    <td className="py-2 pr-4"><span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-bold text-gray-600">{c.lead_status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="rounded-2xl border border-gray-200/70 bg-white p-4 shadow-sm">
          <h2 className="text-sm font-bold text-gray-900">Recent Activity</h2>
          <ul className="mt-2 space-y-2">
            {activity.length === 0 && <li className="text-xs text-gray-400">No email events yet.</li>}
            {activity.slice(0, 7).map((a, i) => (
              <li key={i} className="flex items-center gap-2 text-xs">
                <span className={`h-2 w-2 shrink-0 rounded-full ${a.type === 'reply' ? 'bg-emerald-500' : a.type === 'bounce' || a.type === 'failed' ? 'bg-rose-500' : 'bg-primary-500'}`} />
                <span className="font-semibold text-gray-700">{a.type}</span>
                <span className="ml-auto text-[11px] text-gray-400">{new Date(a.at).toLocaleDateString()}</span>
              </li>
            ))}
          </ul>
          <Link to="/activity" className="mt-3 block text-[11px] font-semibold text-primary-600">Open email activity</Link>
        </div>
      </div>
    </div>
  );
}
