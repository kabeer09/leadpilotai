import { useState } from 'react';
import { Sparkles, AlertTriangle, ListChecks, ShieldCheck } from 'lucide-react';
import { aiTradeAnalyst } from '../../lib/api';
import { errText, useCountries, SourceBanner, Notice, Country } from './shared';

// AI Trade Analyst: a narrative over a trade payload.
// Honesty contract: the server assembles FACTS from the real payload and
// the narrative may only restate those facts. Every response carries
// engine + ai_generated + fallback_reason, plus the full facts list so
// the numbers can be audited line by line.

type Kind = 'statistics' | 'competitors' | 'compare';

interface Analysis {
  record_type: string;
  kind: Kind;
  engine: string;
  ai_generated: boolean;
  fallback_reason?: string | null;
  facts: string[];
  narrative: string;
  source?: Record<string, unknown>;
  stale: boolean;
  cached: boolean;
  note?: string;
}

const KINDS: { key: Kind; label: string; blurb: string }[] = [
  { key: 'statistics', label: 'Trade statistics',
    blurb: 'Reporter import/export series from UN Comtrade' },
  { key: 'competitors', label: 'Competitor analysis',
    blurb: 'Who supplies a market (country-level ranking)' },
  { key: 'compare', label: 'Country compare',
    blurb: 'Side-by-side totals for 2–4 countries' },
];

const engineBadge = (a: Analysis) => {
  if (a.engine === 'gemini') {
    return <span className="badge bg-purple-100 text-purple-700">AI narrative · Gemini</span>;
  }
  return (
    <span className="badge bg-gray-100 text-gray-600">
      Deterministic template{a.ai_generated ? '' : ' · not AI'}
    </span>
  );
};

export default function AnalystTab() {
  const countries = useCountries();
  const [kind, setKind] = useState<Kind>('statistics');
  const [reporter, setReporter] = useState('');
  const [dest, setDest] = useState('');
  const [codes, setCodes] = useState<number[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [from, setFrom] = useState('2019');
  const [to, setTo] = useState('2023');
  const [period, setPeriod] = useState('2023');
  const [flow, setFlow] = useState('both');
  const [hs, setHs] = useState('');
  const [topN, setTopN] = useState('15');
  const [result, setResult] = useState<Analysis | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const addCode = (code: number) => {
    setAddOpen(false);
    if (codes.includes(code)) return;
    if (codes.length >= 4) return setErr('Comparison is limited to 4 countries');
    setErr('');
    setCodes([...codes, code]);
  };

  const run = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setErr('');
    setResult(null);
    const p: Record<string, unknown> = {};
    if (kind === 'statistics') {
      if (!reporter.trim()) return setErr('Pick a reporter country');
      p.reporter_code = Number(reporter);
      p.period_from = from;
      p.period_to = to;
      p.flow = flow;
    } else if (kind === 'competitors') {
      if (!dest.trim()) return setErr('Pick a destination market');
      p.destination_code = Number(dest);
      p.period = period;
      p.top_n = Number(topN) || 15;
    } else {
      if (codes.length < 2) return setErr('Pick at least 2 countries to compare');
      p.reporter_codes = codes;
      p.period_from = from;
      p.period_to = to;
      p.flow = flow;
    }
    if (hs.trim()) p.cmd_code = hs.trim();

    setBusy(true);
    try {
      const d: Analysis = await aiTradeAnalyst({ kind, params: p });
      setResult(d);
      if (d.stale) setErr('Provider rate-limited — showing cached data');
    } catch (e2) { setErr(errText(e2)); }
    finally { setBusy(false); }
  };

  const numOpts = (v: string, set: (s: string) => void) => (
    <input className="input mt-1" type="number" min="1990" max="2100"
           value={v} onChange={(e) => set(e.target.value)} />
  );

  return (
    <div className="space-y-5">
      <form onSubmit={run} className="card space-y-4">
        <div className="flex items-start gap-2">
          <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-purple-500" />
          <div>
            <p className="text-sm font-semibold text-gray-800">AI Trade Analyst</p>
            <p className="text-xs text-gray-500">
              The narrative is written only from computed facts — every number
              shown below comes from the payload, and the engine used is labelled.
            </p>
          </div>
        </div>

        <div className="grid gap-3 md:grid-cols-3">
          {KINDS.map((k) => (
            <button key={k.key} type="button" onClick={() => { setKind(k.key); setResult(null); setErr(''); }}
                    className={`rounded-xl border p-3 text-left transition-colors ${
                      kind === k.key
                        ? 'border-purple-400 bg-purple-50'
                        : 'border-gray-200 hover:border-gray-300'}`}>
              <span className="block text-sm font-semibold text-gray-800">{k.label}</span>
              <span className="mt-0.5 block text-xs text-gray-500">{k.blurb}</span>
            </button>
          ))}
        </div>

        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {kind === 'statistics' && (
            <>
              <div>
                <label className="label">Reporter country</label>
                <select className="input mt-1" value={reporter}
                        onChange={(e) => setReporter(e.target.value)}>
                  <option value="">Select…</option>
                  {countries.map((c: Country) => (
                    <option key={c.code} value={c.code}>{c.name}</option>
                  ))}
                </select>
              </div>
              <div><label className="label">From year</label>{numOpts(from, setFrom)}</div>
              <div><label className="label">To year</label>{numOpts(to, setTo)}</div>
              <div>
                <label className="label">Flow</label>
                <select className="input mt-1" value={flow} onChange={(e) => setFlow(e.target.value)}>
                  <option value="both">Imports + Exports</option>
                  <option value="M">Imports only</option>
                  <option value="X">Exports only</option>
                </select>
              </div>
            </>
          )}

          {kind === 'competitors' && (
            <>
              <div>
                <label className="label">Destination market</label>
                <select className="input mt-1" value={dest}
                        onChange={(e) => setDest(e.target.value)}>
                  <option value="">Select…</option>
                  {countries.map((c: Country) => (
                    <option key={c.code} value={c.code}>{c.name}</option>
                  ))}
                </select>
              </div>
              <div><label className="label">Period (year)</label>{numOpts(period, setPeriod)}</div>
              <div>
                <label className="label">Top N suppliers</label>
                <input className="input mt-1" type="number" min="1" max="100"
                       value={topN} onChange={(e) => setTopN(e.target.value)} />
              </div>
            </>
          )}

          {kind === 'compare' && (
            <div className="md:col-span-2 lg:col-span-3">
              <label className="label">
                Countries (2–4)
                <span className="ml-2 font-normal text-gray-400">{codes.length}/4 selected</span>
              </label>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                {codes.map((code) => {
                  const c = countries.find((x) => x.code === code);
                  return (
                    <span key={code}
                          className="rounded-full border border-purple-200 bg-purple-50 px-3 py-1 text-xs font-medium text-purple-700">
                      {c?.name || code}
                    </span>
                  );
                })}
                <div className="relative">
                  <button type="button" className="btn-secondary"
                          onClick={() => setAddOpen(!addOpen)} disabled={codes.length >= 4}>
                    Add country
                  </button>
                  {addOpen && (
                    <div className="absolute left-0 z-20 mt-1 max-h-64 w-64 overflow-y-auto rounded-xl border border-gray-200 bg-white shadow-lg">
                      {countries.filter((c) => !codes.includes(c.code)).map((c) => (
                        <button key={c.code} type="button"
                                className="block w-full px-3 py-2 text-left text-sm hover:bg-gray-50"
                                onClick={() => addCode(c.code)}>
                          {c.name} {c.iso2 ? `(${c.iso2})` : ''}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          <div>
            <label className="label">HS code (optional)</label>
            <input className="input mt-1" value={hs} placeholder="e.g. 7113"
                   onChange={(e) => setHs(e.target.value)} />
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button type="submit" className="btn-primary" disabled={busy}>
            <Sparkles className="mr-1.5 h-4 w-4" />
            {busy ? 'Analysing…' : 'Run analysis'}
          </button>
          <span className="text-xs text-gray-400">
            Narratives restate computed facts only — no invented figures.
          </span>
        </div>
      </form>

      {err && <Notice kind={err.includes('cached') ? 'stale' : 'error'}>{err}</Notice>}

      {result && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            {engineBadge(result)}
            {result.ai_generated && (
              <span className="badge bg-green-100 text-green-700">ai_generated: true</span>
            )}
            {result.fallback_reason && (
              <span className="badge bg-amber-100 text-amber-700">
                fallback: {result.fallback_reason}
              </span>
            )}
            {result.stale && <span className="badge bg-amber-100 text-amber-700">stale cache</span>}
            {result.cached && !result.stale && (
              <span className="badge bg-blue-100 text-blue-700">cached</span>
            )}
          </div>

          {result.source && (
            <SourceBanner source={result.source as never} note={result.note} />
          )}

          <div className="card">
            <h3 className="flex items-center gap-2 font-semibold">
              <Sparkles className="h-4 w-4 text-purple-500" /> Narrative
            </h3>
            <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-gray-700">
              {result.narrative}
            </p>
            {!result.ai_generated && (
              <p className="mt-3 flex items-start gap-1.5 text-xs text-gray-400">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                Not AI-generated: a deterministic template restating the computed
                facts{result.fallback_reason ? ` (${result.fallback_reason})` : ''}.
              </p>
            )}
          </div>

          <div className="card">
            <h3 className="flex items-center gap-2 font-semibold">
              <ListChecks className="h-4 w-4 text-gray-500" /> Facts used
              <span className="ml-1 text-xs font-normal text-gray-400">
                ({result.facts.length})
              </span>
            </h3>
            <p className="mt-1 flex items-start gap-1.5 text-xs text-gray-400">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              Every number in the narrative appears in this list — audit it line by line.
            </p>
            <ul className="mt-2 space-y-1.5">
              {result.facts.map((f, i) => (
                <li key={i}
                    className="rounded-lg bg-gray-50 px-3 py-1.5 font-mono text-xs text-gray-700">
                  {f}
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}
