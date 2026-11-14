import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from 'recharts';
import { Building2, Mail, Send, MessageSquare, ArrowUpRight, MoreHorizontal, Target, Flame } from 'lucide-react';
import { overview, trends, listCompanies, listFollowups, analyticsFunnel, crmPipeline } from '../lib/api';
import { useAuth } from '../auth';

interface Ov { [k: string]: number }
interface Day { date: string; discovered: number; sent: number }
interface Stage { key: string; label: string; count: number; pct_of_previous: number | null; pct_of_first: number | null }
interface Funnel {
  stages: Stage[];
  rates: {
    delivery_rate_pct: number | null; reply_rate_pct: number | null;
    bounce_rate_pct: number | null; followups_due: number;
    denominators: { attempted: number; delivered: number; bounced: number; replies: number };
  };
  note: string;
}
interface Band { band: string; count: number; pct: number | null }
interface Pipeline {
  scope: { scored_companies: number; companies_in_scope: number; sampled: boolean };
  lead_score_bands: Band[];
  research_coverage: { pct_evidence: number | null; pct_evaluation: number | null };
  contact_coverage: { pct_email: number | null };
  lead_score_note: string;
}

// null means "no denominator" — render an em dash, NEVER a fake 0%.
const pctOrDash = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v}%`);
const BAND_STYLE: Record<string, string> = {
  Hot: 'bg-rose-100 text-rose-700 border-rose-200',
  Warm: 'bg-amber-100 text-amber-700 border-amber-200',
  Nurture: 'bg-sky-100 text-sky-700 border-sky-200',
  Low: 'bg-gray-100 text-gray-600 border-gray-200',
};

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
  const [funnel, setFunnel] = useState<Funnel | null>(null);
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);

  useEffect(() => {
    overview(30).then((o) => {
      setData(o);
      setActivity(o.recent_activity || []);
    }).catch(() => {});
    trends(30).then((t) => setDaily(t.daily)).catch(() => {});
    listCompanies({ page: 1, page_size: 5 }).then((d) => setRecent(d.items)).catch(() => {});
    listFollowups().then((f) => setDue(f.filter((x: { status: string }) => x.status === 'SCHEDULED').slice(0, 5))).catch(() => {});
    analyticsFunnel().then(setFunnel).catch(() => {});
    crmPipeline().then(setPipeline).catch(() => {});
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

      {/* Conversion funnel + lead-score bands (persisted rows only) */}
      {(funnel || pipeline) && (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
          <div className="rounded-2xl border border-gray-200/70 bg-white p-4 shadow-sm xl:col-span-2">
            <div className="flex items-center justify-between">
              <h2 className="flex items-center gap-1.5 text-sm font-bold text-gray-900">
                <Target className="h-3.5 w-3.5 text-primary-500" /> Conversion Funnel
              </h2>
              <Link to="/analytics" className="text-[11px] font-semibold text-primary-600">View all</Link>
            </div>
            {funnel ? (
              <div className="mt-3 space-y-2">
                {funnel.stages.map((s, i) => {
                  const first = funnel.stages[0]?.count ?? 0;
                  const w = first > 0 ? Math.max(3, Math.round((s.count / first) * 100)) : 3;
                  return (
                    <div key={s.key} className="flex items-center gap-3">
                      <span className="w-40 shrink-0 truncate text-[11px] font-medium text-gray-600" title={s.label}>{s.label}</span>
                      <div className="h-5 flex-1 overflow-hidden rounded-full bg-gray-100">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-primary-500 to-primary-600 transition-all"
                          style={{ width: `${w}%` }}
                        />
                      </div>
                      <span className="w-12 shrink-0 text-right text-[12px] font-bold text-gray-900">{s.count.toLocaleString()}</span>
                      <span className="w-14 shrink-0 text-right text-[11px] text-gray-400">
                        {i === 0 ? '—' : pctOrDash(s.pct_of_previous)}
                      </span>
                    </div>
                  );
                })}
                <div className="flex items-center justify-between border-t border-gray-100 pt-2 text-[11px] text-gray-400">
                  <span>Each stage is a subset of the one above · rates are “—” until something is sent</span>
                  <span className="font-semibold text-gray-500">
                    {funnel.rates.denominators.attempted} attempted · reply {pctOrDash(funnel.rates.reply_rate_pct)}
                  </span>
                </div>
              </div>
            ) : (
              <p className="mt-3 text-xs text-gray-400">Loading funnel…</p>
            )}
          </div>

          <div className="rounded-2xl border border-gray-200/70 bg-white p-4 shadow-sm">
            <h2 className="flex items-center gap-1.5 text-sm font-bold text-gray-900">
              <Flame className="h-3.5 w-3.5 text-amber-500" /> Lead Score Bands
            </h2>
            {pipeline ? (
              <>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  {pipeline.lead_score_bands.map((b) => (
                    <div key={b.band} className={`rounded-xl border p-2.5 text-center ${BAND_STYLE[b.band] || 'bg-gray-100 text-gray-600 border-gray-200'}`}>
                      <p className="text-lg font-bold">{b.count.toLocaleString()}</p>
                      <p className="text-[10px] font-bold uppercase tracking-wide">{b.band}</p>
                      <p className="text-[10px] opacity-70">{pctOrDash(b.pct)}</p>
                    </div>
                  ))}
                </div>
                <p className="mt-2 text-[10px] leading-relaxed text-gray-400">{pipeline.lead_score_note}</p>
                <div className="mt-2 flex items-center justify-between text-[11px] text-gray-500">
                  <span>{pipeline.research_coverage.pct_evidence === null ? '—' : `${pipeline.research_coverage.pct_evidence}%` } researched</span>
                  <span>{pipeline.contact_coverage.pct_email === null ? '—' : `${pipeline.contact_coverage.pct_email}%` } with email</span>
                </div>
              </>
            ) : (
              <p className="mt-3 text-xs text-gray-400">Loading bands…</p>
            )}
          </div>
        </div>
      )}

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
