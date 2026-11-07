import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend,
} from 'recharts';
import {
  Search, Save, Trash2, Download, RefreshCw, Database,
  AlertTriangle, Building2, Info,
} from 'lucide-react';
import {
  tradeSearch, tradeStatistics, tradeHsCodes, tradeCountries,
  tradeListSaved, tradeSaveSearch, tradeDeleteSaved, tradeExport,
  dataSources,
} from '../lib/api';

const errText = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
  (e as Error)?.message || 'Request failed';

interface Country { code: number; name: string; iso2?: string }
interface TradeRow {
  period?: string; flowCode?: string; flowDesc?: string;
  reporterDesc?: string; reporterCode?: number;
  partnerDesc?: string; partnerCode?: number;
  cmdCode?: string; cmdDesc?: string;
  primaryValue?: number | null; fobvalue?: number | null;
  cifvalue?: number | null; qty?: number | null; qtyUnitAbbr?: string;
  netWgt?: number | null;
}
interface SearchResp {
  record_type: string;
  source: { name?: string; url?: string; coverage?: string; disclaimer?: string; periods?: string[] };
  count: number; truncated: boolean; stale: boolean; cached: boolean;
  stale_reason?: string; rows: TradeRow[]; fetched_at?: string;
}
interface SeriesPoint { period: string; flow: string; value: number; records: number }
interface StatsResp {
  record_type: string;
  source: { name?: string; disclaimer?: string };
  series: SeriesPoint[]; total_value: number;
  stale: boolean; cached: boolean; stale_reason?: string;
}
interface SavedSearch {
  id: number; name: string; kind: string;
  query: Record<string, string | number>;
}
interface HsItem { code: string; description: string; is_leaf: boolean }
interface SourceItem {
  key: string; name: string; endpoint?: string; status: string;
  mode?: string; limits?: string; auth_configured?: boolean;
  last_success_at?: string; last_error?: string; coverage_note?: string;
}

const fmtMoney = (v?: number | null) =>
  v == null ? '—' : new Intl.NumberFormat('en-US', {
    notation: 'compact', maximumFractionDigits: 1,
  }).format(v) + ' USD';

const fmtFull = (v?: number | null) =>
  v == null ? '—' : new Intl.NumberFormat('en-US').format(v);

const flowLabel = (f?: string) =>
  f === 'M' ? 'Import' : f === 'X' ? 'Export' : f || '—';

export default function GlobalTrade() {
  // ---- filters ----
  const [reporter, setReporter] = useState('');
  const [flow, setFlow] = useState('both');
  const [from, setFrom] = useState('2023');
  const [to, setTo] = useState('2023');
  const [hs, setHs] = useState('');
  const [partner, setPartner] = useState('');

  // ---- reference data ----
  const [reporters, setReporters] = useState<Country[]>([]);
  const [partners, setPartners] = useState<Country[]>([]);
  const [hsSuggestions, setHsSuggestions] = useState<HsItem[]>([]);

  // ---- results ----
  const [result, setResult] = useState<SearchResp | null>(null);
  const [stats, setStats] = useState<StatsResp | null>(null);
  const [saved, setSaved] = useState<SavedSearch[]>([]);
  const [sources, setSources] = useState<SourceItem[]>([]);
  const [saveName, setSaveName] = useState('');

  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(''), 4000); };

  const params = useCallback(() => {
    const p: Record<string, string | number> = {
      reporter_code: Number(reporter), period_from: from, period_to: to, flow,
    };
    if (hs.trim()) p.cmd_code = hs.trim();
    if (partner !== '') p.partner_code = Number(partner);
    return p;
  }, [reporter, from, to, flow, hs, partner]);

  const loadSaved = useCallback(() => {
    tradeListSaved().then((d) => setSaved(d.items || [])).catch(() => {});
  }, []);

  const loadSources = useCallback(() => {
    dataSources().then((d) => setSources(d.items || [])).catch(() => {});
  }, []);

  useEffect(() => {
    tradeCountries()
      .then((d) => { setReporters(d.reporters || []); setPartners(d.partners || []); })
      .catch((e) => setErr(errText(e)));
    loadSaved(); loadSources();
  }, [loadSaved, loadSources]);

  // HS typeahead (debounced, only for non-numeric text)
  useEffect(() => {
    const q = hs.trim();
    if (q.length < 2 || /^\d+$/.test(q)) { setHsSuggestions([]); return; }
    const t = setTimeout(() => {
      tradeHsCodes({ q, limit: 8 })
        .then((d) => setHsSuggestions(d.items || []))
        .catch(() => setHsSuggestions([]));
    }, 350);
    return () => clearTimeout(t);
  }, [hs]);

  const runSearch = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setErr('');
    if (!reporter) return setErr('Choose a reporter country first');
    setBusy(true);
    try {
      const p = params();
      const [s, st] = await Promise.all([
        tradeSearch(p), tradeStatistics(p),
      ]);
      setResult(s); setStats(st);
      if (s.stale) setErr(`Showing cached data: ${s.stale_reason || 'provider rate-limited'}`);
    } catch (e2) { setErr(errText(e2)); }
    finally { setBusy(false); }
  };

  const saveCurrent = async () => {
    setErr('');
    if (!saveName.trim()) return setErr('Give the search a name');
    try {
      await tradeSaveSearch({ name: saveName.trim(), kind: 'trade', query: params() });
      setSaveName(''); flash('Search saved'); loadSaved();
    } catch (e) { setErr(errText(e)); }
  };

  const loadSavedSearch = (s: SavedSearch) => {
    const q = s.query || {};
    if (q.reporter_code) setReporter(String(q.reporter_code));
    if (q.period_from) setFrom(String(q.period_from));
    if (q.period_to) setTo(String(q.period_to));
    if (q.flow) setFlow(String(q.flow));
    setHs(q.cmd_code ? String(q.cmd_code) : '');
    setPartner(q.partner_code != null ? String(q.partner_code) : '');
    flash(`Loaded "${s.name}" — press Search`);
  };

  const removeSaved = async (s: SavedSearch) => {
    try { await tradeDeleteSaved(s.id); flash('Deleted'); loadSaved(); }
    catch (e) { setErr(errText(e)); }
  };

  const download = async (format: 'csv' | 'xlsx') => {
    setErr('');
    if (!reporter) return setErr('Run a search first');
    setBusy(true);
    try {
      const blob = await tradeExport(params(), format);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `leadpilot-trade-${Date.now()}.${format}`;
      a.click(); URL.revokeObjectURL(url);
      flash(`Downloaded ${format.toUpperCase()}`);
    } catch (e) { setErr(errText(e)); }
    finally { setBusy(false); }
  };

  // chart data: M and X as separate lines keyed by period
  const trendData = useMemo(() => {
    if (!stats?.series?.length) return [];
    const byPeriod: Record<string, { period: string; imports?: number; exports?: number }> = {};
    for (const p of stats.series) {
      const cell = (byPeriod[p.period] ||= { period: p.period });
      if (p.flow === 'M') cell.imports = p.value;
      else if (p.flow === 'X') cell.exports = p.value;
    }
    return Object.values(byPeriod).sort((a, b) => a.period.localeCompare(b.period));
  }, [stats]);

  const src = result?.source;

  return (
    <div className="space-y-5 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Global Trade Data</h1>
          <p className="text-sm text-gray-500">
            Aggregated import/export statistics from UN Comtrade — not shipment records
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn-secondary" onClick={() => download('csv')}
                  disabled={busy}>
            <Download className="mr-1.5 h-4 w-4" /> CSV
          </button>
          <button className="btn-secondary" onClick={() => download('xlsx')}
                  disabled={busy}>
            <Download className="mr-1.5 h-4 w-4" /> Excel
          </button>
        </div>
      </div>

      {msg && <div className="rounded-xl bg-green-50 px-4 py-2 text-sm text-green-700">{msg}</div>}
      {err && (
        <div className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-2 text-sm text-red-700">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {err}
        </div>
      )}

      {/* ---- search form ---- */}
      <form onSubmit={runSearch} className="card grid gap-4 md:grid-cols-3 lg:grid-cols-6">
        <div className="lg:col-span-2">
          <label className="label">Reporter country *</label>
          <select className="input mt-1" value={reporter}
                  onChange={(e) => setReporter(e.target.value)} required>
            <option value="">Select country…</option>
            {reporters.map((c) => (
              <option key={c.code} value={c.code}>
                {c.name} {c.iso2 ? `(${c.iso2})` : ''}
              </option>
            ))}
          </select>
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
          <label className="label">Partner (optional)</label>
          <select className="input mt-1" value={partner}
                  onChange={(e) => setPartner(e.target.value)}>
            <option value="">All partners</option>
            {partners.map((c) => (
              <option key={c.code} value={c.code}>{c.name}</option>
            ))}
          </select>
        </div>
        <div className="relative lg:col-span-2">
          <label className="label">HS code / product</label>
          <input className="input mt-1" list="hs-suggestions" value={hs}
                 placeholder="e.g. 7113 or 'jewellery'"
                 onChange={(e) => setHs(e.target.value)} />
          <datalist id="hs-suggestions">
            {hsSuggestions.map((h) => (
              <option key={h.code} value={h.code}>{h.description}</option>
            ))}
          </datalist>
        </div>
        <div className="flex items-end gap-2 lg:col-span-4">
          <button type="submit" className="btn-primary flex-1" disabled={busy}>
            <Search className="mr-1.5 h-4 w-4" />
            {busy ? 'Searching…' : 'Search trade data'}
          </button>
          <input className="input flex-1" placeholder="Name this search to save…"
                 value={saveName} onChange={(e) => setSaveName(e.target.value)} />
          <button type="button" className="btn-lime" onClick={saveCurrent}
                  disabled={busy}>
            <Save className="mr-1.5 h-4 w-4" /> Save
          </button>
        </div>
      </form>

      {/* ---- saved searches ---- */}
      {saved.length > 0 && (
        <div className="card">
          <h2 className="mb-3 text-sm font-semibold text-gray-700">Saved searches</h2>
          <div className="flex flex-wrap gap-2">
            {saved.map((s) => (
              <div key={s.id}
                   className="flex items-center gap-1.5 rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-xs">
                <button className="font-medium text-gray-700 hover:text-black"
                        onClick={() => loadSavedSearch(s)}>{s.name}</button>
                <button className="text-gray-400 hover:text-red-600"
                        onClick={() => removeSaved(s)} title="Delete">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ---- source banner ---- */}
      {src && (
        <div className="flex flex-wrap items-start gap-x-6 gap-y-2 rounded-2xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
          <span className="flex items-center gap-1.5 font-semibold">
            <Database className="h-4 w-4" /> Source: {src.name || 'UN Comtrade'}
          </span>
          <span>Record type: <b>aggregated statistics</b> (not shipments)</span>
          {src.coverage && <span>Coverage: {src.coverage}</span>}
          {!!src.periods?.length && <span>Periods: {src.periods.join(', ')}</span>}
          {result?.fetched_at && <span>Updated: {result.fetched_at}</span>}
          {result?.truncated && (
            <span className="text-amber-700">⚠ Result capped at 500 records/call — narrow the query</span>
          )}
          {result?.cached && !result.stale && <span className="text-blue-600">(cached)</span>}
          {src.disclaimer && (
            <span className="flex items-start gap-1 basis-full text-xs text-blue-700">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {src.disclaimer}
            </span>
          )}
        </div>
      )}

      {/* ---- trend chart ---- */}
      {trendData.length > 0 && (
        <div className="card">
          <h2 className="mb-3 text-sm font-semibold text-gray-700">
            Trade value trend
            {stats?.total_value ? (
              <span className="ml-2 font-normal text-gray-400">
                total {fmtFull(stats.total_value)} USD
              </span>
            ) : null}
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
      )}

      {/* ---- results table ---- */}
      <div className="card overflow-x-auto">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-700">
            Results {result ? `(${result.count} rows)` : ''}
          </h2>
          <Link to={`/companies?q=${encodeURIComponent(hs.trim() || src?.coverage || '')}`}
                className="flex items-center gap-1.5 text-xs font-medium text-primary-700 hover:underline">
            <Building2 className="h-3.5 w-3.5" /> Find matching companies in CRM
          </Link>
        </div>
        {!result && (
          <p className="py-8 text-center text-sm text-gray-400">
            Pick a reporter country and press Search to load trade statistics.
          </p>
        )}
        {result && result.count === 0 && (
          <p className="py-8 text-center text-sm text-gray-400">
            No records for this query in UN Comtrade — try another year, flow or HS code.
          </p>
        )}
        {result && result.count > 0 && (
          <table className="min-w-full">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="table-th">Year</th>
                <th className="table-th">Flow</th>
                <th className="table-th">Reporter</th>
                <th className="table-th">Partner</th>
                <th className="table-th">HS code</th>
                <th className="table-th">Product</th>
                <th className="table-th text-right">Trade value (USD)</th>
                <th className="table-th text-right">Qty</th>
                <th className="table-th text-right">Net weight (kg)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {result.rows.map((r, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="table-td">{r.period || '—'}</td>
                  <td className="table-td">
                    <span className={`badge ${r.flowCode === 'X'
                      ? 'bg-green-100 text-green-700'
                      : 'bg-indigo-100 text-indigo-700'}`}>
                      {r.flowDesc || flowLabel(r.flowCode)}
                    </span>
                  </td>
                  <td className="table-td">{r.reporterDesc || r.reporterCode || '—'}</td>
                  <td className="table-td">{r.partnerDesc || (r.partnerCode ?? '—')}</td>
                  <td className="table-td font-mono text-xs">{r.cmdCode || '—'}</td>
                  <td className="table-td max-w-[260px] truncate" title={r.cmdDesc || ''}>
                    {r.cmdDesc || '—'}
                  </td>
                  <td className="table-td text-right font-medium">
                    {fmtFull(r.primaryValue ?? r.cifvalue ?? r.fobvalue)}
                  </td>
                  <td className="table-td text-right">
                    {r.qty ? `${fmtFull(r.qty)}${r.qtyUnitAbbr ? ' ' + r.qtyUnitAbbr : ''}` : '—'}
                  </td>
                  <td className="table-td text-right">{r.netWgt ? fmtFull(r.netWgt) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* ---- value by partner bar chart (from raw rows) ---- */}
      {result && result.count > 0 && (
        <div className="card">
          <h2 className="mb-3 text-sm font-semibold text-gray-700">
            Top partners by trade value
          </h2>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={(() => {
              const agg: Record<string, { name: string; value: number }> = {};
              for (const r of result.rows) {
                const name = r.partnerDesc || String(r.partnerCode ?? 'Unknown');
                const v = r.primaryValue ?? r.cifvalue ?? r.fobvalue ?? 0;
                const cell = (agg[name] ||= { name, value: 0 });
                cell.value += typeof v === 'number' ? v : 0;
              }
              return Object.values(agg).sort((a, b) => b.value - a.value).slice(0, 10);
            })()}>
              <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
              <XAxis dataKey="name" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }}
                     tickFormatter={(v) => fmtMoney(v).replace(' USD', '')} />
              <Tooltip formatter={(v: number) => fmtFull(v) + ' USD'} />
              <Bar dataKey="value" fill="#6366f1" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* ---- data sources ---- */}
      <div className="card">
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
          <RefreshCw className="h-4 w-4" /> Data sources
        </h2>
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {sources.map((s) => (
            <div key={s.key}
                 className="rounded-xl border border-gray-100 bg-gray-50 p-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-gray-800">{s.name}</span>
                <span className={`badge ${s.status === 'active'
                  ? 'bg-green-100 text-green-700'
                  : s.status === 'error' ? 'bg-red-100 text-red-700'
                  : 'bg-gray-100 text-gray-500'}`}>
                  {s.status}
                </span>
              </div>
              {s.mode && <p className="mt-1 text-xs text-gray-500">{s.mode}</p>}
              {s.limits && <p className="mt-1 text-xs text-gray-400">{s.limits}</p>}
              {s.coverage_note && (
                <p className="mt-1 text-xs text-gray-400">{s.coverage_note}</p>
              )}
              {s.last_success_at && (
                <p className="mt-1 text-xs text-green-600">
                  Last success: {String(s.last_success_at).replace('T', ' ').slice(0, 16)} UTC
                </p>
              )}
              {s.last_error && (
                <p className="mt-1 truncate text-xs text-red-500"
                   title={s.last_error}>Error: {s.last_error}</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
