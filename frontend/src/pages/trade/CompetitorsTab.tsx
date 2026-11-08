import { useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { Search, AlertTriangle, Building2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import { tradeCompetitors } from '../../lib/api';
import { errText, fmtFull, fmtMoney, useCountries, SourceBanner, Notice, Country } from './shared';

// Competitor analysis: who supplies a market (destination imports ranked by
// partner country). Aggregated COUNTRY-level statistics only — UN Comtrade
// does not expose company identities, and the payload says so.

interface Supplier {
  rank: number; partner_code: number; partner_desc?: string | null;
  value: number; share_pct: number; records: number;
}
interface CompetitorsResp {
  record_type: string;
  source: { name?: string; coverage?: string; disclaimer?: string; periods?: string[];
            destination_name?: string };
  suppliers: Supplier[]; total_value: number; partners_count: number;
  periods: string[]; flows: string[]; truncated: boolean; dropped_world: boolean;
  stale: boolean; cached: boolean; stale_reason?: string;
  note?: string;
}

export default function CompetitorsTab() {
  const countries = useCountries();
  const [destination, setDestination] = useState('');
  const [hs, setHs] = useState('');
  const [period, setPeriod] = useState('2023');
  const [topN, setTopN] = useState('15');
  const [result, setResult] = useState<CompetitorsResp | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const run = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setErr('');
    if (!destination) return setErr('Choose a destination market first');
    setBusy(true);
    try {
      const p: Record<string, string | number> = {
        destination_code: Number(destination), period, top_n: Number(topN),
      };
      if (hs.trim()) p.cmd_code = hs.trim();
      const d = await tradeCompetitors(p);
      setResult(d);
      if (d.stale) setErr(`Showing cached data: ${d.stale_reason || 'provider rate-limited'}`);
    } catch (e2) { setErr(errText(e2)); setResult(null); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-5">
      <form onSubmit={run} className="card grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <div>
          <label className="label">Destination market *</label>
          <select className="input mt-1" value={destination}
                  onChange={(e) => setDestination(e.target.value)} required>
            <option value="">Select import market…</option>
            {countries.map((c: Country) => (
              <option key={c.code} value={c.code}>
                {c.name} {c.iso2 ? `(${c.iso2})` : ''}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">HS product code (optional)</label>
          <input className="input mt-1" value={hs} placeholder="e.g. 7113"
                 onChange={(e) => setHs(e.target.value)} />
        </div>
        <div>
          <label className="label">Year</label>
          <input className="input mt-1" type="number" min="1990" max="2100"
                 value={period} onChange={(e) => setPeriod(e.target.value)} />
        </div>
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <label className="label">Top N suppliers</label>
            <input className="input mt-1" type="number" min="1" max="100"
                   value={topN} onChange={(e) => setTopN(e.target.value)} />
          </div>
          <button type="submit" className="btn-primary" disabled={busy}>
            <Search className="mr-1.5 h-4 w-4" />
            {busy ? 'Analysing…' : 'Find suppliers'}
          </button>
        </div>
      </form>

      {err && <Notice kind={err.includes('cached') ? 'stale' : 'error'}>{err}</Notice>}

      {result && (
        <SourceBanner source={result.source}
          extra={
            <>
              {result.stale && <span className="text-amber-700">(stale cache)</span>}
              {result.cached && !result.stale && <span className="text-blue-600">(cached)</span>}
              {result.truncated && (
                <span className="text-amber-700">⚠ Partners capped — refine the query</span>
              )}
              {result.dropped_world && (
                <span>World aggregate excluded (double-count guard)</span>
              )}
            </>
          }
          note={result.note} />
      )}

      {result && result.suppliers.length === 0 && (
        <div className="card py-10 text-center text-sm text-gray-400">
          No partner records for this market/year in UN Comtrade — try another
          year or drop the HS code filter.
        </div>
      )}

      {result && result.suppliers.length > 0 && (
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="card overflow-x-auto">
            <h2 className="mb-3 text-sm font-semibold text-gray-700">
              Supplier countries → {result.source.destination_name || 'market'}
              <span className="ml-2 font-normal text-gray-400">
                {result.partners_count} partners, {fmtFull(result.total_value)} USD total
              </span>
            </h2>
            <table className="min-w-full">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className="table-th">#</th>
                  <th className="table-th">Supplier country</th>
                  <th className="table-th text-right">Trade value (USD)</th>
                  <th className="table-th text-right">Share</th>
                  <th className="table-th text-right">Records</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {result.suppliers.map((s) => (
                  <tr key={s.partner_code} className="hover:bg-gray-50">
                    <td className="table-td text-gray-400">{s.rank}</td>
                    <td className="table-td font-medium">
                      {s.partner_desc || s.partner_code}
                    </td>
                    <td className="table-td text-right">{fmtFull(s.value)}</td>
                    <td className="table-td text-right">{s.share_pct}%</td>
                    <td className="table-td text-right text-gray-400">{s.records}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-4 flex items-start gap-2 rounded-xl bg-gray-50 p-3 text-xs text-gray-500">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                Country-level export sources only — trade statistics do not
                identify individual companies.{' '}
                <Link to="/discovery" className="font-medium text-primary-700 hover:underline">
                  <Building2 className="mr-1 inline h-3 w-3" />
                  Find company-level leads in Buyer Discovery
                </Link>
              </span>
            </div>
          </div>

          <div className="card">
            <h2 className="mb-3 text-sm font-semibold text-gray-700">
              Top suppliers by import value
            </h2>
            <ResponsiveContainer width="100%" height={320}>
              <BarChart data={result.suppliers.slice(0, 10).map((s) => ({
                name: s.partner_desc || String(s.partner_code), value: s.value,
              }))}>
                <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
                <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }}
                       tickFormatter={(v) => fmtMoney(v).replace(' USD', '')} />
                <Tooltip formatter={(v: number) => fmtFull(v) + ' USD'} />
                <Bar dataKey="value" fill="#6366f1" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}
