import { useState } from 'react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from 'recharts';
import { Search, Info, ChevronRight } from 'lucide-react';
import { tradeHsIntel } from '../../lib/api';
import { errText, fmtFull, fmtMoney, useCountries, SourceBanner, Notice, Country } from './shared';

// HS intelligence: official UN classification hierarchy (ancestors/children)
// + optional reporter trade trend for the code. Unknown codes surface the
// API's honest 404 — never an invented description.

interface HsNode {
  code: string; description: string; aggr_level?: number; is_leaf?: boolean;
}
interface Classification extends HsNode {
  record_type: string;
  source: { name?: string; url?: string; disclaimer?: string };
  chapter?: HsNode | null; ancestors: HsNode[]; children: HsNode[];
}
interface SeriesPoint { period: string; flow: string; value: number; records: number }
interface Trend {
  record_type: string;
  source: { name?: string; disclaimer?: string; periods?: string[] };
  series: SeriesPoint[]; total_value: number;
  stale: boolean; cached: boolean; stale_reason?: string;
}
interface HsIntelResp {
  record_type: string;
  classification: Classification;
  trend: Trend | null;
  note?: string;
}

const fmtLevel = (n?: number) =>
  n === 2 ? 'Chapter' : n === 4 ? 'Heading' : n === 6 ? 'Subheading' : `Level ${n ?? '?'}`;

export default function HsIntelTab() {
  const countries = useCountries();
  const [code, setCode] = useState('');
  const [reporter, setReporter] = useState('');
  const [from, setFrom] = useState('2019');
  const [to, setTo] = useState('2023');
  const [flow, setFlow] = useState('both');
  const [result, setResult] = useState<HsIntelResp | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const run = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setErr('');
    if (!code.trim()) return setErr('Enter an HS code (e.g. 7113)');
    setBusy(true);
    try {
      const p: Record<string, string | number> = { cmd_code: code.trim() };
      if (reporter) {
        p.reporter_code = Number(reporter);
        p.period_from = from; p.period_to = to; p.flow = flow;
      }
      const d = await tradeHsIntel(p);
      setResult(d);
      if (d.trend?.stale)
        setErr(`Trend shows cached data: ${d.trend.stale_reason || 'provider rate-limited'}`);
    } catch (e2) { setErr(errText(e2)); setResult(null); }
    finally { setBusy(false); }
  };

  const cls = result?.classification;
  const trend = result?.trend;

  // series -> per-period imports/exports lines
  const trendData = (() => {
    if (!trend?.series?.length) return [];
    const byPeriod: Record<string, { period: string; imports?: number; exports?: number }> = {};
    for (const p of trend.series) {
      const cell = (byPeriod[p.period] ||= { period: p.period });
      if (p.flow === 'M') cell.imports = p.value;
      else if (p.flow === 'X') cell.exports = p.value;
    }
    return Object.values(byPeriod).sort((a, b) => a.period.localeCompare(b.period));
  })();

  return (
    <div className="space-y-5">
      <form onSubmit={run} className="card grid gap-4 md:grid-cols-2 lg:grid-cols-5">
        <div>
          <label className="label">HS code *</label>
          <input className="input mt-1" value={code} placeholder="e.g. 7113"
                 onChange={(e) => setCode(e.target.value)} required />
        </div>
        <div>
          <label className="label">Country trend (optional)</label>
          <select className="input mt-1" value={reporter}
                  onChange={(e) => setReporter(e.target.value)}>
            <option value="">No trend — classification only</option>
            {countries.map((c: Country) => (
              <option key={c.code} value={c.code}>{c.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">From year</label>
          <input className="input mt-1" type="number" min="1990" max="2100"
                 value={from} disabled={!reporter}
                 onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div>
          <label className="label">To year</label>
          <input className="input mt-1" type="number" min="1990" max="2100"
                 value={to} disabled={!reporter}
                 onChange={(e) => setTo(e.target.value)} />
        </div>
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <label className="label">Flow</label>
            <select className="input mt-1" value={flow} disabled={!reporter}
                    onChange={(e) => setFlow(e.target.value)}>
              <option value="both">Imports + Exports</option>
              <option value="M">Imports only</option>
              <option value="X">Exports only</option>
            </select>
          </div>
          <button type="submit" className="btn-primary" disabled={busy}>
            <Search className="mr-1.5 h-4 w-4" />
            {busy ? 'Loading…' : 'Inspect'}
          </button>
        </div>
      </form>

      {err && <Notice kind={err.includes('cached') ? 'stale' : 'error'}>{err}</Notice>}

      {cls && (
        <div className="card">
          <div className="flex flex-wrap items-center gap-1.5 text-sm text-gray-500">
            <span className="font-mono">{cls.chapter?.code || cls.ancestors[0]?.code || cls.code}</span>
            {cls.ancestors.map((a) => (
              <span key={a.code} className="flex items-center gap-1.5">
                <ChevronRight className="h-3.5 w-3.5 text-gray-300" />
                <span className="font-mono">{a.code}</span>
                <span className="text-gray-400">{a.description}</span>
              </span>
            ))}
            <ChevronRight className="h-3.5 w-3.5 text-gray-300" />
            <span className="font-mono font-semibold text-gray-900">{cls.code}</span>
          </div>
          <h2 className="mt-2 text-lg font-bold text-gray-900">{cls.description}</h2>
          <div className="mt-2 flex flex-wrap gap-2">
            <span className="badge bg-indigo-100 text-indigo-700">{fmtLevel(cls.aggr_level)}</span>
            <span className={`badge ${cls.is_leaf
              ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'}`}>
              {cls.is_leaf ? 'Leaf (trade-reportable)' : 'Grouping heading'}
            </span>
          </div>

          {cls.children.length > 0 && (
            <div className="mt-4 overflow-x-auto">
              <h3 className="mb-2 text-sm font-semibold text-gray-700">
                Sub-headings under {cls.code}
              </h3>
              <table className="min-w-full">
                <thead>
                  <tr className="border-b border-gray-100">
                    <th className="table-th">Code</th>
                    <th className="table-th">Description</th>
                    <th className="table-th">Level</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {cls.children.map((c) => (
                    <tr key={c.code} className="hover:bg-gray-50">
                      <td className="table-td font-mono text-xs">{c.code}</td>
                      <td className="table-td">{c.description}</td>
                      <td className="table-td text-gray-500">{fmtLevel(c.aggr_level)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="mt-4 flex items-start gap-2 rounded-xl bg-gray-50 p-3 text-xs text-gray-500">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              Hierarchy from the official UN Comtrade HS reference — unknown
              codes return an honest "not found", never an invented description.
              {cls.source.disclaimer ? ` ${cls.source.disclaimer}` : ''}
            </span>
          </div>
        </div>
      )}

      {trend && (
        <>
          <SourceBanner source={trend.source}
            extra={
              <>
                {trend.stale && <span className="text-amber-700">(stale cache)</span>}
                {trend.cached && !trend.stale && <span className="text-blue-600">(cached)</span>}
                <span>Total: {fmtFull(trend.total_value)} USD</span>
              </>
            } />
          {trendData.length > 0 ? (
            <div className="card">
              <h2 className="mb-3 text-sm font-semibold text-gray-700">
                Trade trend for {code.trim()}
                <span className="ml-2 font-normal text-gray-400">
                  aggregated statistics — not shipment records
                </span>
              </h2>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={trendData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
                  <XAxis dataKey="period" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }}
                         tickFormatter={(v) => fmtMoney(v).replace(' USD', '')} />
                  <Tooltip formatter={(v: number) => fmtFull(v) + ' USD'} />
                  <Legend />
                  <Line type="monotone" dataKey="imports" stroke="#6366f1"
                        strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="exports" stroke="#22c55e"
                        strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="card py-8 text-center text-sm text-gray-400">
              No trade records for this code/country/period in UN Comtrade —
              try a different year range or flow.
            </div>
          )}
        </>
      )}
    </div>
  );
}
