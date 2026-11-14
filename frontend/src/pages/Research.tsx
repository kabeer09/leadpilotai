import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Search, RefreshCw, Mail, Globe, Phone, AlertTriangle, CheckCircle,
  FileText, Star, Building2, Link2, Sparkles, Gauge,
} from 'lucide-react';
import {
  researchPickCompanies, researchReport, researchReportRefresh,
  generateDraft, listQualified, aiResearchSummary, aiLeadScore,
} from '../lib/api';

const errText = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
  (e as Error)?.message || 'Request failed';

interface Pick { id: number; name: string; country?: string; website?: string;
  email_count: number; phone_count: number; has_research: boolean;
  research_score?: number; }
interface Evidence {
  id: number; contact_type: string; raw_value: string; source_url: string;
  extraction_method?: string; confidence?: number;
  validation_status: string; last_observed: string;
}
interface Evaluation {
  buyer_fit_score?: number; buyer_type?: string; product_fit?: string;
  country_fit?: string; recommended_next_action?: string;
  missing_evidence?: string[]; supporting_snippets?: string[];
  emails?: string[]; phones?: string[];
  contact_completeness_score?: number; evidence_quality_score?: number;
  validation_coverage?: number; evaluated_at?: string;
}
interface Report {
  record_type: string; note: string;
  company: { id: number; name: string; website?: string; country?: string;
    city?: string; category?: string; hall?: string; booth?: string;
    fair?: string; product_summary?: string;
    products: { name?: string }[]; profile_url?: string;
    last_verified?: string };
  contacts: { id: number; kind: string; value: string; source_url?: string;
    verification: string }[];
  evidence: Evidence[];
  evaluation: Evaluation | null;
  research: any; has_research: boolean; refreshed_at?: string;
}
interface QualRow { company_id: number; name: string; score: number;
  buyer_type: string; evaluated_at?: string; }
interface AiSummary {
  record_type: string; kind: string; engine: string; ai_generated: boolean;
  fallback_reason?: string | null; facts: string[]; narrative: string;
  note?: string;
}
interface LeadScore {
  record_type: string; company: { id: number; name: string };
  score: number; max: number; band: string;
  factors: { key: string; label: string; points: number; max: number;
             reason: string }[];
  engine: string; ai_generated: boolean; note?: string;
  inputs: Record<string, unknown>;
}

export default function Research() {
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState('');
  const [picks, setPicks] = useState<Pick[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [draft, setDraft] = useState<{ subject: string; body: string } | null>(null);
  const [ai, setAi] = useState<AiSummary | null>(null);
  const [leadScore, setLeadScore] = useState<LeadScore | null>(null);
  const [qualified, setQualified] = useState<QualRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const initRef = useRef(false);

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(''), 4000); };

  const loadReport = useCallback(async (id: number) => {
    setErr('');
    try {
      const r: Report = await researchReport(id);
      setReport(r); setDraft(null); setAi(null); setLeadScore(null);
    } catch (e) { setErr(errText(e)); }
  }, []);

  // picker search (debounced)
  useEffect(() => {
    const q = query.trim();
    if (q.length < 1) { setPicks([]); return; }
    const t = setTimeout(() => {
      researchPickCompanies({ q, limit: 8 })
        .then((d) => setPicks(d.items || []))
        .catch(() => setPicks([]));
    }, 300);
    return () => clearTimeout(t);
  }, [query]);

  // deep link: /research?company=123 (from discovery / CRM)
  useEffect(() => {
    const id = Number(params.get('company'));
    if (id && !initRef.current) {
      initRef.current = true;
      loadReport(id);
    }
  }, [params, loadReport]);

  // persisted qualified list (fast path, no network)
  useEffect(() => {
    listQualified(40).then((d) => setQualified(d.companies || [])).catch(() => {});
  }, []);

  const refresh = async () => {
    if (!report) return;
    setLoading(true); setErr('');
    try {
      const r: Report = await researchReportRefresh(report.company.id);
      setReport(r);
      flash('Live research complete — evidence + scores saved');
    } catch (e) { setErr(errText(e)); }
    finally { setLoading(false); }
  };

  const makeDraft = async () => {
    if (!report) return;
    setLoading(true); setErr('');
    try {
      const r = await generateDraft(report.company.id);
      setDraft(r.draft);
    } catch (e) { setErr(errText(e)); }
    finally { setLoading(false); }
  };

  // AI summary: narrative assembled server-side from this report's
  // persisted rows only (zero network) — engine label travels with it.
  const runAiSummary = async () => {
    if (!report) return;
    setLoading(true); setErr('');
    try {
      setAi(await aiResearchSummary(report.company.id));
    } catch (e) { setErr(errText(e)); }
    finally { setLoading(false); }
  };

  // Explainable lead score: factors carry points/max/reason and sum
  // exactly to the score (deterministic — no LLM in the scoring path).
  const runLeadScore = async () => {
    if (!report) return;
    setLoading(true); setErr('');
    try {
      setLeadScore(await aiLeadScore(report.company.id));
    } catch (e) { setErr(errText(e)); }
    finally { setLoading(false); }
  };

  const qual = report?.evaluation;
  const live = report?.research;
  const score = qual?.buyer_fit_score ?? 0;

  return (
    <div className="space-y-4 p-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Buyer Research & Qualification</h1>
        <p className="text-sm text-gray-500">
          Durable research reports from real website runs — evidence rows keep
          the source URL for every contact. Nothing is guessed.
        </p>
      </div>

      {msg && <div className="rounded-xl bg-green-50 px-4 py-2 text-sm text-green-700">{msg}</div>}
      {err && (
        <div className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-2 text-sm text-red-700">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {err}
        </div>
      )}

      {/* company picker */}
      <div className="card relative">
        <label className="label">Find a company</label>
        <div className="relative">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
          <input
            className="input pl-9"
            placeholder="Type a company name (e.g. Payne, Ambiente exhibitor…)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        {picks.length > 0 && (
          <div className="mt-2 divide-y divide-gray-50 rounded-xl border border-gray-100">
            {picks.map((p) => (
              <button key={p.id}
                      className="flex w-full items-center justify-between px-3 py-2 text-left hover:bg-gray-50"
                      onClick={() => { setPicks([]); setQuery(p.name); loadReport(p.id); }}>
                <span>
                  <span className="text-sm font-medium text-gray-900">{p.name}</span>
                  <span className="ml-2 text-xs text-gray-400">
                    {p.country || '—'} · {p.email_count} email · {p.phone_count} phone
                  </span>
                </span>
                {p.has_research ? (
                  <span className="badge bg-green-100 text-green-700">
                    researched · {p.research_score ?? '—'}
                  </span>
                ) : (
                  <span className="badge bg-gray-100 text-gray-500">not researched</span>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {report && (
        <>
          {/* profile + score */}
          <div className="card flex flex-wrap items-start justify-between gap-4">
            <div>
              <h2 className="flex items-center gap-2 text-lg font-semibold">
                <Building2 className="h-5 w-5 text-gray-400" />
                {report.company.name}
              </h2>
              <p className="text-sm text-gray-500">
                {[report.company.country, report.company.city,
                  report.company.category].filter(Boolean).join(' · ') || '—'}
              </p>
              {report.company.fair && (
                <p className="text-xs text-gray-400">
                  {report.company.fair}
                  {report.company.hall ? ` · ${report.company.hall}` : ''}
                  {report.company.booth ? ` · booth ${report.company.booth}` : ''}
                </p>
              )}
              <div className="mt-1 flex flex-wrap gap-3 text-xs">
                {report.company.website && (
                  <a href={report.company.website} target="_blank" rel="noreferrer"
                     className="flex items-center gap-1 text-primary-700 hover:underline">
                    <Globe className="h-3.5 w-3.5" /> {report.company.website}
                  </a>
                )}
                {report.company.profile_url && (
                  <a href={report.company.profile_url} target="_blank" rel="noreferrer"
                     className="flex items-center gap-1 text-gray-500 hover:underline">
                    <Link2 className="h-3.5 w-3.5" /> Fair profile
                  </a>
                )}
              </div>
            </div>
            <div className="flex items-center gap-3">
              <div className={`flex h-14 w-14 items-center justify-center rounded-full text-xl font-bold text-white
                ${score >= 70 ? 'bg-green-500' : score >= 40 ? 'bg-yellow-500' : 'bg-gray-400'}`}>
                {qual ? score : '—'}
              </div>
              <div className="text-sm">
                {qual ? (
                  <>
                    <p className="font-semibold">{qual.buyer_type}</p>
                    <p className="text-xs text-gray-500">{qual.product_fit}</p>
                    <p className="text-xs text-gray-400">
                      {qual.evaluated_at
                        ? `evaluated ${new Date(qual.evaluated_at).toLocaleString()}`
                        : ''}
                    </p>
                  </>
                ) : (
                  <p className="text-xs text-gray-400">No research run yet</p>
                )}
              </div>
            </div>
            <div className="flex gap-2">
              <button className="btn-primary text-sm" onClick={refresh} disabled={loading}>
                <RefreshCw className={`mr-1.5 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                {loading ? 'Researching…' : 'Run live research'}
              </button>
              <button className="btn-secondary text-sm" onClick={makeDraft} disabled={loading}>
                <FileText className="mr-1.5 h-4 w-4" /> Draft
              </button>
              <button className="btn-secondary text-sm" onClick={runAiSummary} disabled={loading}>
                <Sparkles className="mr-1.5 h-4 w-4" /> AI summary
              </button>
              <button className="btn-secondary text-sm" onClick={runLeadScore} disabled={loading}>
                <Gauge className="mr-1.5 h-4 w-4" /> Lead score
              </button>
            </div>
          </div>

          {/* AI summary: labelled narrative over persisted rows only */}
          {ai && (
            <div className="card">
              <h3 className="flex flex-wrap items-center gap-2 font-semibold">
                <Sparkles className="h-4 w-4 text-purple-500" /> AI summary
                <span className={`badge ${ai.engine === 'gemini'
                  ? 'bg-purple-100 text-purple-700'
                  : 'bg-gray-100 text-gray-600'}`}>
                  {ai.engine === 'gemini' ? 'AI narrative · Gemini'
                    : 'Deterministic template'}
                </span>
                {ai.fallback_reason && (
                  <span className="badge bg-amber-100 text-amber-700">
                    fallback: {ai.fallback_reason}
                  </span>
                )}
              </h3>
              <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-gray-700">
                {ai.narrative}
              </p>
              <details className="mt-3">
                <summary className="cursor-pointer text-xs text-gray-400">
                  Show the {ai.facts.length} facts this was built from
                </summary>
                <ul className="mt-2 space-y-1">
                  {ai.facts.map((f, i) => (
                    <li key={i}
                        className="rounded-lg bg-gray-50 px-3 py-1.5 font-mono text-xs text-gray-700">
                      {f}
                    </li>
                  ))}
                </ul>
              </details>
              <p className="mt-2 text-xs text-gray-400">{ai.note}</p>
            </div>
          )}

          {/* Lead score: explainable, factors sum exactly to the score */}
          {leadScore && (
            <div className="card">
              <h3 className="flex flex-wrap items-center gap-2 font-semibold">
                <Gauge className="h-4 w-4 text-indigo-500" /> Lead score
                <span className="badge bg-indigo-100 text-indigo-700">
                  {leadScore.engine}
                </span>
                <span className="text-xs font-normal text-gray-400">
                  {leadScore.ai_generated ? 'AI-generated' : 'deterministic — not AI'}
                </span>
              </h3>
              <div className="mt-3 flex items-center gap-4">
                <div className={`flex h-16 w-16 items-center justify-center rounded-full text-xl font-bold text-white
                  ${leadScore.band === 'Hot' ? 'bg-green-500'
                    : leadScore.band === 'Warm' ? 'bg-yellow-500'
                    : leadScore.band === 'Nurture' ? 'bg-blue-500' : 'bg-gray-400'}`}>
                  {leadScore.score}
                </div>
                <div>
                  <p className="text-sm font-semibold text-gray-800">
                    {leadScore.band} · {leadScore.score}/{leadScore.max}
                  </p>
                  <p className="text-xs text-gray-500">
                    Every factor below carries its points, maximum and reason —
                    they add up to exactly {leadScore.score}.
                  </p>
                </div>
              </div>
              <div className="mt-3 space-y-2">
                {leadScore.factors.map((f) => (
                  <div key={f.key}
                       className="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2">
                    <div className="flex items-center justify-between text-sm">
                      <span className="font-medium text-gray-800">{f.label}</span>
                      <span className="font-mono text-xs text-gray-600">
                        {f.points} / {f.max}
                      </span>
                    </div>
                    <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-gray-200">
                      <div className="h-full rounded-full bg-indigo-500"
                           style={{ width: `${Math.round((f.points / f.max) * 100)}%` }} />
                    </div>
                    <p className="mt-1 text-xs text-gray-500">{f.reason}</p>
                  </div>
                ))}
              </div>
              <p className="mt-3 text-xs text-gray-400">{leadScore.note}</p>
            </div>
          )}

          {/* qualification detail */}
          {qual && (
            <div className="card">
              <h3 className="flex items-center gap-2 font-semibold">
                <Star className="h-4 w-4 text-yellow-500" /> Qualification
              </h3>
              <div className="mt-2 grid gap-3 text-sm md:grid-cols-3">
                <div><span className="text-gray-500">Product fit:</span> {qual.product_fit}</div>
                <div><span className="text-gray-500">Country fit:</span> {qual.country_fit}</div>
                <div><span className="text-gray-500">Next action:</span> {qual.recommended_next_action}</div>
              </div>
              {qual.supporting_snippets && qual.supporting_snippets.length > 0 && (
                <ul className="mt-2 space-y-1 text-xs text-gray-500">
                  {qual.supporting_snippets.map((s, i) => <li key={i}>• {s}</li>)}
                </ul>
              )}
              {qual.missing_evidence && qual.missing_evidence.length > 0 && (
                <div className="mt-2">
                  <p className="text-xs font-medium text-yellow-700">Missing evidence:</p>
                  <ul className="text-xs text-yellow-600">
                    {qual.missing_evidence.map((m, i) => <li key={i}>• {m}</li>)}
                  </ul>
                </div>
              )}
              <div className="mt-3 flex gap-4 text-xs text-gray-500">
                <span>contact completeness: {qual.contact_completeness_score ?? '—'}%</span>
                <span>evidence quality: {qual.evidence_quality_score ?? '—'}%</span>
                <span>validation coverage: {qual.validation_coverage ?? '—'}%</span>
              </div>
            </div>
          )}

          {/* live research output (only after refresh) */}
          {live && (
            <div className="card">
              <h3 className="font-semibold">Live research result</h3>
              <div className="mt-2 space-y-2">
                {(live.emails || []).map((e: any, i: number) => (
                  <div key={i} className="flex items-center gap-2 text-sm">
                    <Mail className="h-4 w-4 text-gray-400" />
                    <span>{e.value}</span>
                    <span className="badge bg-blue-100 text-blue-700 text-xs">{e.extraction_method}</span>
                    <a href={e.source_url} target="_blank" rel="noreferrer"
                       className="text-xs text-gray-400 hover:underline">
                      source ({new URL(e.source_url).hostname})
                    </a>
                  </div>
                ))}
                {(live.phones || []).map((p: any, i: number) => (
                  <div key={i} className="flex items-center gap-2 text-sm">
                    <Phone className="h-4 w-4 text-gray-400" />
                    <span>{p.value}</span>
                    <span className="badge bg-blue-100 text-blue-700 text-xs">{p.extraction_method}</span>
                  </div>
                ))}
                {live.description && <p className="text-xs text-gray-500">{live.description}</p>}
                {(live.pages_checked || []).length > 0 && (
                  <p className="text-xs text-gray-400">Checked {live.pages_checked.length} pages</p>
                )}
                {(live.errors || []).length > 0 && (
                  <div>
                    {live.errors.map((e: string, i: number) => (
                      <p key={i} className="text-xs text-red-500">{e}</p>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* contacts + evidence (persisted) */}
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="card">
              <h3 className="flex items-center gap-2 font-semibold">
                <CheckCircle className="h-4 w-4 text-green-600" /> Saved contacts
              </h3>
              {report.contacts.length === 0 ? (
                <p className="mt-2 text-sm text-gray-400">No contacts saved yet — run live research.</p>
              ) : (
                <ul className="mt-2 space-y-1.5 text-sm">
                  {report.contacts.map((c) => (
                    <li key={c.id} className="flex items-center gap-2">
                      <span className="badge bg-gray-100 text-gray-600">{c.kind}</span>
                      <span>{c.value}</span>
                      {c.source_url && (
                        <a href={c.source_url} target="_blank" rel="noreferrer"
                           className="text-xs text-gray-400 hover:underline">source</a>
                      )}
                      <span className="text-xs text-gray-400">{c.verification}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="card">
              <h3 className="font-semibold">Research evidence</h3>
              <p className="text-xs text-gray-400">Every field keeps its source URL + extraction method.</p>
              {report.evidence.length === 0 ? (
                <p className="mt-2 text-sm text-gray-400">No evidence rows yet.</p>
              ) : (
                <ul className="mt-2 space-y-1.5 text-xs">
                  {report.evidence.slice(0, 12).map((ev) => (
                    <li key={ev.id} className="flex items-center gap-2">
                      <span className="badge bg-gray-100 text-gray-600">{ev.contact_type}</span>
                      <span className="font-medium">{ev.raw_value}</span>
                      <span className="text-gray-400">{ev.extraction_method}</span>
                      <a href={ev.source_url} target="_blank" rel="noreferrer"
                         className="text-gray-400 hover:underline">
                        {ev.confidence != null ? `${Math.round(ev.confidence * 100)}%` : 'source'}
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          {/* outreach draft */}
          <div className="card">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">Outreach Draft</h3>
              {!draft && (
                <button onClick={makeDraft} disabled={loading} className="btn-secondary text-sm">
                  Generate Draft
                </button>
              )}
            </div>
            {draft && (
              <div className="mt-3 space-y-2">
                <p className="text-sm font-medium">{draft.subject}</p>
                <pre className="whitespace-pre-wrap rounded bg-gray-50 p-3 text-xs text-gray-700">{draft.body}</pre>
                <p className="text-xs text-yellow-600">⚠️ Requires human approval before sending. Auto-send is disabled.</p>
              </div>
            )}
          </div>
        </>
      )}

      {/* persisted qualified list */}
      <div className="card">
        <h3 className="flex items-center gap-2 font-semibold">
          <Star className="h-4 w-4 text-yellow-500" /> Qualified companies
          <span className="text-xs font-normal text-gray-400">
            (persisted scores — no website calls)
          </span>
        </h3>
        {qualified.length === 0 ? (
          <p className="mt-2 text-sm text-gray-400">
            No persisted evaluations yet — run live research on a company above.
          </p>
        ) : (
          <div className="mt-2 flex flex-wrap gap-2">
            {qualified.slice(0, 20).map((q) => (
              <button key={q.company_id}
                      className="rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-xs hover:bg-gray-100"
                      onClick={() => loadReport(q.company_id)}>
                <b>{q.score}</b> · {q.name} · {q.buyer_type}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
