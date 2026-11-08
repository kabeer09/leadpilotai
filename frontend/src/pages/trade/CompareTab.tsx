import { useState } from 'react';
import { Plus, X, Scale, AlertTriangle } from 'lucide-react';
import { tradeCompare } from '../../lib/api';
import { errText, fmtFull, useCountries, SourceBanner, Notice, Country } from './shared';

// Side-by-side country comparison (aggregated annual statistics).
// Shares are each country's piece of the COMPARED set — labelled as such,
// never as a share of world trade.

interface CompareCountry {
  reporter_code: number; name: string;
  series: { period: string; flow: string; value: number; records: number }[];
  total_value: number; stale: boolean; cached: boolean;
}
interface MatrixRow {
  period: string; flow: string;
  values: Record<string, number>;
  combined: number;
  shares: Record<string, number>;
}
interface CompareResp {
  record_type: string;
  source: { name?: string; coverage?: string; disclaimer?: string; periods?: string[] };
  params: { reporter_codes: number[]; period_from: string; period_to: string;
            flow: string; cmd_code?: string | null };
  countries: CompareCountry[];
  matrix: MatrixRow[];
  stale: boolean; cached: boolean;
  note?: string;
}

const flowLabel = (f: string) => (f === 'M' ? 'Import' : f === 'X' ? 'Export' : f || '—');

export default function CompareTab() {
  const countries = useCountries();
  const [picked, setPicked] = useState<number[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [from, setFrom] = useState('2022');
  const [to, setTo] = useState('2023');
  const [flow, setFlow] = useState('both');
  const [hs, setHs] = useState('');
  const [result, setResult] = useState<CompareResp | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const add = (code: number) => {
    setAddOpen(false);
    if (picked.includes(code)) return;
    if (picked.length >= 4) return setErr('Comparison is limited to 4 countries');
    setErr('');
    setPicked([...picked, code]);
  };
  const remove = (code: number) => setPicked(picked.filter((c) => c !== code));

  const run = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setErr('');
    if (picked.length < 2) return setErr('Pick at least 2 countries to compare');
    setBusy(true);
    try {
      const p: Record<string, string | number> = {
        reporter_codes: picked.join(','), period_from: from, period_to: to, flow,
      };
      if (hs.trim()) p.cmd_code = hs.trim();
      const d = await tradeCompare(p);
      setResult(d);
      if (d.stale) setErr('Some countries are showing cached data (provider rate-limited)');
    } catch (e2) { setErr(errText(e2)); setResult(null); }
    finally { setBusy(false); }
  };

  const nameOf = (code: number | string) =>
    result?.countries.find((c) => String(c.reporter_code) === String(code))?.name ||
    countries.find((c) => c.code === Number(code))?.name || String(code);

  const available = countries.filter((c) => !picked.includes(c.code));

  return (
    <div className="space-y-5">
      <form onSubmit={run} className="card space-y-4">
        <div>
          <label className="label">
            Countries to compare (2–4)
            <span className="ml-2 font-normal text-gray-400">{picked.length}/4 selected</span>
          </label>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            {picked.map((code) => {
              const c = countries.find((x) => x.code === code);
              return (
                <span key={code}
                      className="flex items-center gap-1.5 rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-700">
                  {c?.name || code}
                  <button type="button" onClick={() => remove(code)} title="Remove">
                    <X className="h-3.5 w-3.5" />
                  </button>
                </span>
              );
            })}
            <div className="relative">
              <button type="button" className="btn-secondary"
                      onClick={() => setAddOpen(!addOpen)} disabled={picked.length >= 4}>
                <Plus className="mr-1 h-4 w-4" /> Add country
              </button>
              {addOpen && (
                <div className="absolute left-0 z-20 mt-1 max-h-64 w-64 overflow-y-auto rounded-xl border border-gray-200 bg-white shadow-lg">
                  {available.map((c) => (
                    <button key={c.code} type="button"
                            className="block w-full px-3 py-2 text-left text-sm hover:bg-gray-50"
                            onClick={() => add(c.code)}>
                      {c.name} {c.iso2 ? `(${c.iso2})` : ''}
                    </button>
                  ))}
                  {available.length === 0 && (
                    <p className="px-3 py-2 text-sm text-gray-400">No more countries</p>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <div>
            <label className="label">From year</label>
            <input className="input mt-1" type="number" min="1990" max="2100"
                   value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div>
            <label className="label">To year</label>
            <input className="input mt-1" type="number" min="1990" max="2100"
                   value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
          <div>
            <label className="label">Flow</label>
            <select className="input mt-1" value={flow}
                    onChange={(e) => setFlow(e.target.value)}>
              <option value="both">Imports + Exports</option>
              <option value="M">Imports only</option>
              <option value="X">Exports only</option>
            </select>
          </div>
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <label className="label">HS code (optional)</label>
              <input className="input mt-1" value={hs} placeholder="e.g. 7113"
                     onChange={(e) => setHs(e.target.value)} />
            </div>
            <button type="submit" className="btn-primary" disabled={busy}>
              <Scale className="mr-1.5 h-4 w-4" />
              {busy ? 'Comparing…' : 'Compare'}
            </button>
          </div>
        </div>
        <p className="flex items-start gap-1.5 text-xs text-gray-400">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          Max 4 countries and 5 years per query (each country-year is a separate
          provider call — keeps us inside free-tier limits).
        </p>
      </form>

      {err && <Notice kind={err.includes('cached') ? 'stale' : 'error'}>{err}</Notice>}

      {result && (
        <SourceBanner source={result.source}
          extra={
            <>
              {result.stale && <span className="text-amber-700">(stale cache)</span>}
              {result.cached && !result.stale && <span className="text-blue-600">(cached)</span>}
              <span>Countries: {result.countries.map((c) => c.name).join(', ')}</span>
            </>
          }
          note={result.note} />
      )}

      {result && result.countries.length > 0 && (
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
          {result.countries.map((c) => (
            <div key={c.reporter_code} className="card">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-gray-800">{c.name}</h3>
                {c.stale ? (
                  <span className="badge bg-amber-100 text-amber-700">stale</span>
                ) : c.cached ? (
                  <span className="badge bg-blue-100 text-blue-700">cached</span>
                ) : null}
              </div>
              <p className="mt-2 text-xl font-bold text-gray-900">
                {fmtFull(c.total_value)} <span className="text-xs font-normal text-gray-400">USD</span>
              </p>
              <p className="text-xs text-gray-400">{c.series.length} data points</p>
            </div>
          ))}
        </div>
      )}

      {result && result.matrix.length > 0 && (
        <div className="card overflow-x-auto">
          <h2 className="mb-1 text-sm font-semibold text-gray-700">
            Side-by-side matrix
          </h2>
          <p className="mb-3 text-xs text-gray-400">
            Value (share of the compared set) per period/flow — shares are
            between these countries only, not shares of world trade.
          </p>
          <table className="min-w-full">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="table-th">Year</th>
                <th className="table-th">Flow</th>
                {result.countries.map((c) => (
                  <th key={c.reporter_code} className="table-th text-right">{c.name}</th>
                ))}
                <th className="table-th text-right">Combined (USD)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {result.matrix.map((row, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="table-td">{row.period}</td>
                  <td className="table-td">
                    <span className={`badge ${row.flow === 'X'
                      ? 'bg-green-100 text-green-700'
                      : 'bg-indigo-100 text-indigo-700'}`}>
                      {flowLabel(row.flow)}
                    </span>
                  </td>
                  {result.countries.map((c) => {
                    const code = String(c.reporter_code);
                    const v = row.values[code] ?? 0;
                    const share = row.shares[code] ?? 0;
                    return (
                      <td key={code} className="table-td text-right">
                        <span className="font-medium">{fmtFull(v)}</span>
                        <span className="ml-1.5 text-xs text-gray-400">{share}%</span>
                      </td>
                    );
                  })}
                  <td className="table-td text-right font-medium">
                    {fmtFull(row.combined)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {result && result.matrix.length === 0 && (
        <div className="card py-10 text-center text-sm text-gray-400">
          No overlapping records for these countries/years in UN Comtrade —
          try a different year range, flow or drop the HS filter.
        </div>
      )}
    </div>
  );
}
