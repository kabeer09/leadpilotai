import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Download, Play, AlertTriangle, Info, MapPin,
  CheckCircle2, XCircle, Loader2,
} from 'lucide-react';
import {
  exhibitorsList, exhibitorsOverview, exhibitorsExtract, exhibitorsExport, getCrawl,
} from '../lib/api';

const errText = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
  (e as Error)?.message || 'Request failed';

interface Fair {
  id: string; name: string; base_url: string; status: string;
  access_method: string; automation_permitted: boolean; notes: string;
  fallback: string; can_extract: boolean;
  sources: { id: number; name: string; url: string; status: string }[];
  jobs_by_status: Record<string, number>;
  exhibitors: number; with_email: number; with_phone: number;
  last_extraction: {
    job_id: number; status: string; url: string; pages_visited: number;
    companies_found: number; started_at?: string; finished_at?: string;
    errors: string[];
  } | null;
  recent_jobs: { job_id: number; status: string; companies_found: number;
                 finished_at?: string }[];
}
interface Overview {
  record_type: string; disclaimer: string;
  summary: { fairs_registered: number; fairs_with_data: number;
             exhibitors_total: number;
             jobs_by_status: Record<string, number> };
  fairs: Fair[];
  unregistered_sources: { sources: { id: number; name: string; url: string }[];
                          jobs_by_status: Record<string, number>; exhibitors: number };
}
interface ExItem {
  id: number; name: string; country?: string; city?: string;
  website?: string; category?: string; hall?: string; booth?: string;
  product_summary?: string; profile_url?: string; source_id?: number;
  fair?: string; extraction_status: string; products: { name?: string }[];
  emails: string[]; phones: string[];
}
interface ListResp {
  record_type: string; disclaimer: string; total: number;
  page: number; page_size: number; status_labels: string[]; items: ExItem[];
}

const STATUS_CHIP: Record<string, string> = {
  Pending: 'bg-gray-100 text-gray-600',
  Running: 'bg-blue-100 text-blue-700 animate-pulse',
  Completed: 'bg-green-100 text-green-700',
  Failed: 'bg-red-100 text-red-700',
  Cancelled: 'bg-gray-200 text-gray-500',
  'Not run': 'bg-gray-50 text-gray-400',
};

function StatusChip({ s }: { s: string }) {
  return (
    <span className={`badge ${STATUS_CHIP[s] || 'bg-gray-100 text-gray-500'}`}>{s}</span>
  );
}

const REG_CHIP: Record<string, string> = {
  READY: 'bg-green-100 text-green-700',
  READY_WITH_LIMITATIONS: 'bg-amber-100 text-amber-700',
  MANUAL_IMPORT_REQUIRED: 'bg-gray-100 text-gray-600',
  AUTHORIZATION_REQUIRED: 'bg-blue-100 text-blue-700',
  TEMPORARILY_UNAVAILABLE: 'bg-red-100 text-red-600',
  DISABLED_BY_POLICY: 'bg-red-100 text-red-600',
  CONFIGURATION_ERROR: 'bg-red-100 text-red-700',
};

export default function Exhibitors() {
  const [ov, setOv] = useState<Overview | null>(null);
  const [q, setQ] = useState('');
  const [hall, setHall] = useState('');
  const [status, setStatus] = useState('');
  const [hasEmail, setHasEmail] = useState('');
  const [sourceId, setSourceId] = useState('');
  const [page, setPage] = useState(1);
  const [data, setData] = useState<ListResp | null>(null);

  const [extractUrl, setExtractUrl] = useState('');
  const [extractPages, setExtractPages] = useState('50');
  const [activeJob, setActiveJob] = useState<{ job_id: number; status: string; fair: string } | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(''), 5000); };

  const loadOverview = useCallback(() => {
    exhibitorsOverview().then(setOv).catch((e) => setErr(errText(e)));
  }, []);

  const filters = useCallback(() => {
    const p: Record<string, string | number | boolean> = { page_size: 25 };
    if (q.trim()) p.q = q.trim();
    if (hall.trim()) p.hall = hall.trim();
    if (status) p.status = status;
    if (hasEmail === 'true') p.has_email = true;
    if (hasEmail === 'false') p.has_email = false;
    if (sourceId) p.source_id = Number(sourceId);
    return p;
  }, [q, hall, status, hasEmail, sourceId]);

  const load = useCallback(async (p?: number) => {
    setBusy(true); setErr('');
    try {
      const body = await exhibitorsList({ ...filters(), page: p || 1 });
      setData(body); setPage(body.page);
    } catch (e) { setErr(errText(e)); }
    finally { setBusy(false); }
  }, [filters]);

  useEffect(() => { load(1); loadOverview(); }, []);
  useEffect(() => {
    const t = setTimeout(() => load(1), 350);
    return () => clearTimeout(t);
  }, [q, hall, status, hasEmail, sourceId]);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const labelOf = (raw: string) =>
    ({ PENDING: 'Pending', CRAWLING: 'Running', COMPLETED: 'Completed',
       FAILED: 'Failed', CANCELLED: 'Cancelled' } as Record<string, string>)[raw] || raw;

  const startExtract = async () => {
    if (!extractUrl.trim()) return setErr('Enter a registered fair URL to extract');
    setErr('');
    try {
      const r = await exhibitorsExtract({ url: extractUrl.trim(), max_pages: Number(extractPages) || 50 });
      setActiveJob({ job_id: r.job_id, status: labelOf(r.status), fair: r.fair });
      flash(`Extraction queued for ${r.fair}`);
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const j = await getCrawl(r.job_id);
          setActiveJob({ job_id: r.job_id, status: labelOf(j.status), fair: r.fair });
          if (['COMPLETED', 'FAILED', 'CANCELLED'].includes(j.status)) {
            if (pollRef.current) clearInterval(pollRef.current);
            flash(j.status === 'COMPLETED'
              ? `Extraction finished — ${j.companies_found} companies found`
              : `Extraction ${j.status.toLowerCase()}`);
            load(1); loadOverview();
          }
        } catch { /* transient poll error — keep trying */ }
      }, 3000);
    } catch (e) { setErr(errText(e)); }
  };

  const download = async () => {
    try {
      const blob = await exhibitorsExport(filters());
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `exhibitors-${Date.now()}.csv`;
      a.click(); URL.revokeObjectURL(url);
      flash('Downloaded CSV');
    } catch (e) { setErr(errText(e)); }
  };

  const totalPages = data ? Math.max(1, Math.ceil(data.total / 25)) : 1;
  const s = ov?.summary;

  return (
    <div className="space-y-5 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Exhibitor Dashboard</h1>
          <p className="text-sm text-gray-500">
            Trade-fair extraction status, searchable exhibitor directory, export
          </p>
        </div>
        <button className="btn-secondary" onClick={download} disabled={busy}>
          <Download className="mr-1.5 h-4 w-4" /> Export CSV
        </button>
      </div>

      {msg && <div className="rounded-xl bg-green-50 px-4 py-2 text-sm text-green-700">{msg}</div>}
      {err && (
        <div className="flex items-start gap-2 rounded-xl bg-red-50 px-4 py-2 text-sm text-red-700">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {err}
        </div>
      )}

      <div className="flex items-start gap-2 rounded-2xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <span>
          <b>Fair directory listings.</b> {ov?.disclaimer ||
            'Participation indicates presence at a fair — not current buying/selling activity.'}
        </span>
      </div>

      {activeJob && (
        <div className="flex items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm">
          {activeJob.status === 'Pending' || activeJob.status === 'Running'
            ? <Loader2 className="h-4 w-4 animate-spin text-blue-600" />
            : activeJob.status === 'Completed'
              ? <CheckCircle2 className="h-4 w-4 text-green-600" />
              : <XCircle className="h-4 w-4 text-red-600" />}
          <span className="font-medium">{activeJob.fair}</span>
          <StatusChip s={activeJob.status} />
          <span className="text-xs text-gray-400">job #{activeJob.job_id} — polling…</span>
        </div>
      )}

      {/* summary + launch */}
      {s && (
        <div className="grid gap-3 md:grid-cols-4">
          <div className="card">
            <p className="text-xs text-gray-500">Registered fairs with data</p>
            <p className="text-2xl font-bold">{s.fairs_with_data}<span className="text-sm font-normal text-gray-400">/{s.fairs_registered}</span></p>
          </div>
          <div className="card">
            <p className="text-xs text-gray-500">Exhibitors in workspace</p>
            <p className="text-2xl font-bold">{s.exhibitors_total}</p>
          </div>
          <div className="card md:col-span-2">
            <p className="text-xs text-gray-500">Extraction jobs by status</p>
            <div className="mt-1 flex flex-wrap gap-2">
              {Object.entries(s.jobs_by_status).map(([k, v]) => (
                <span key={k} className={`badge ${STATUS_CHIP[k] || 'bg-gray-100 text-gray-500'}`}>
                  {k}: {v}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* fair cards */}
      {ov && (
        <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
          {ov.fairs.map((f) => (
            <div key={f.id} className="card">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900">{f.name}</h3>
                  <p className="truncate text-xs text-gray-400" title={f.base_url}>{f.base_url}</p>
                </div>
                <span className={`badge shrink-0 ${REG_CHIP[f.status] || 'bg-gray-100 text-gray-500'}`}>
                  {f.status.replace(/_/g, ' ')}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {Object.entries(f.jobs_by_status).filter(([, v]) => v > 0).map(([k, v]) => (
                  <span key={k} className={`badge ${STATUS_CHIP[k] || 'bg-gray-100 text-gray-500'}`}>{k}: {v}</span>
                ))}
                {Object.values(f.jobs_by_status).every((v) => v === 0) && (
                  <span className="badge bg-gray-50 text-gray-400">no runs yet</span>
                )}
              </div>
              <div className="mt-2 flex gap-4 text-xs text-gray-500">
                <span>{f.exhibitors} exhibitors</span>
                <span>{f.with_email} w/ email</span>
                <span>{f.with_phone} w/ phone</span>
              </div>
              {f.last_extraction && (
                <p className="mt-1 text-xs text-gray-400">
                  Last run: <StatusChip s={f.last_extraction.status} />{' '}
                  {f.last_extraction.finished_at
                    ? new Date(f.last_extraction.finished_at).toLocaleString()
                    : ''}
                  {f.last_extraction.errors.length > 0 && (
                    <span className="text-red-500" title={f.last_extraction.errors.join('\n')}>
                      {' '}· error
                    </span>
                  )}
                </p>
              )}
              <div className="mt-3 flex gap-2">
                <button
                  className="btn-primary flex-1 text-xs"
                  disabled={!f.can_extract}
                  title={f.can_extract ? `Extract ${f.name}` : f.notes}
                  onClick={() => { setExtractUrl(f.base_url); }}>
                  <Play className="mr-1 h-3.5 w-3.5" /> Queue extraction
                </button>
                {f.sources.length > 0 && (
                  <button className="btn-secondary text-xs"
                          onClick={() => setSourceId(String(f.sources[0].id))}>
                    View {f.exhibitors}
                  </button>
                )}
              </div>
              {!f.can_extract && (
                <p className="mt-1.5 text-[11px] text-amber-700">{f.fallback}</p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* launch bar */}
      <div className="card flex flex-wrap items-end gap-3">
        <div className="flex-1 min-w-[300px]">
          <label className="label">Registered fair URL</label>
          <input className="input mt-1" value={extractUrl}
                 placeholder="https://www.highpointmarket.org/"
                 onChange={(e) => setExtractUrl(e.target.value)} />
        </div>
        <div className="w-28">
          <label className="label">Max pages</label>
          <input className="input mt-1" type="number" min={1} max={500}
                 value={extractPages} onChange={(e) => setExtractPages(e.target.value)} />
        </div>
        <button className="btn-primary" onClick={startExtract} disabled={busy}>
          <Play className="mr-1.5 h-4 w-4" /> Start extraction
        </button>
        <p className="w-full text-xs text-gray-400">
          Only registry-listed, automatable fairs are accepted — the registry's
          capability check runs before any crawl starts.
        </p>
      </div>

      {/* searchable table */}
      <div className="card overflow-x-auto">
        <div className="mb-3 flex flex-wrap items-end gap-3">
          <div className="relative flex-1 min-w-[220px]">
            <label className="label">Search exhibitors</label>
            <input className="input mt-1" value={q} placeholder="name, hall, booth, product…"
                   onChange={(e) => setQ(e.target.value)} />
          </div>
          <div className="w-40">
            <label className="label">Extraction status</label>
            <select className="input mt-1" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Any</option>
              {(data?.status_labels || ['Pending', 'Running', 'Completed', 'Failed', 'Not run']).map((l) => (
                <option key={l} value={l}>{l}</option>
              ))}
            </select>
          </div>
          <div className="w-44">
            <label className="label">Fair / source</label>
            <select className="input mt-1" value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
              <option value="">All fairs</option>
              {(ov?.fairs || []).flatMap((f) => f.sources).map((src) => (
                <option key={src.id} value={src.id}>{src.name}</option>
              ))}
            </select>
          </div>
          <div className="w-28">
            <label className="label">Email?</label>
            <select className="input mt-1" value={hasEmail} onChange={(e) => setHasEmail(e.target.value)}>
              <option value="">Any</option>
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          </div>
          <div className="w-28">
            <label className="label">Hall</label>
            <input className="input mt-1" value={hall} placeholder="Hall 2"
                   onChange={(e) => setHall(e.target.value)} />
          </div>
        </div>

        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-700">
            Exhibitors {data ? `(${data.total})` : ''}
            {busy && <span className="ml-2 font-normal text-gray-400">loading…</span>}
          </h2>
        </div>
        {data && data.total === 0 && (
          <p className="py-8 text-center text-sm text-gray-400">
            No exhibitors match these filters.
          </p>
        )}
        {data && data.items.length > 0 && (
          <table className="min-w-full">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="table-th">Company</th>
                <th className="table-th">Fair</th>
                <th className="table-th">Hall / Booth</th>
                <th className="table-th">Extraction</th>
                <th className="table-th">Category</th>
                <th className="table-th">Products</th>
                <th className="table-th">Contacts</th>
                <th className="table-th">Country</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {data.items.map((it) => (
                <tr key={it.id} className="hover:bg-gray-50">
                  <td className="table-td">
                    <Link to={`/companies/${it.id}`}
                          className="font-medium text-gray-900 hover:text-primary-700">
                      {it.name}
                    </Link>
                    {it.website && (
                      <a href={it.website} target="_blank" rel="noreferrer"
                         className="block text-xs text-gray-400 hover:text-primary-600">
                        {it.website.replace(/^https?:\/\//, '').slice(0, 40)}
                      </a>
                    )}
                  </td>
                  <td className="table-td max-w-[140px] truncate text-xs text-gray-500"
                      title={it.fair || ''}>{it.fair || '—'}</td>
                  <td className="table-td text-xs">
                    {it.hall || it.booth ? (
                      <span className="flex items-center gap-1">
                        <MapPin className="h-3 w-3 text-gray-400" />
                        {[it.hall, it.booth].filter(Boolean).join(' · ')}
                      </span>
                    ) : '—'}
                  </td>
                  <td className="table-td"><StatusChip s={it.extraction_status} /></td>
                  <td className="table-td max-w-[150px] truncate text-xs"
                      title={it.category || ''}>{it.category || '—'}</td>
                  <td className="table-td max-w-[200px] truncate text-xs text-gray-500"
                      title={it.products.map((p) => p.name || '').join(', ')}>
                    {it.products.length > 0
                      ? it.products.map((p) => p.name || '').filter(Boolean).slice(0, 3).join(', ')
                      : '—'}
                  </td>
                  <td className="table-td text-xs">
                    {it.emails.length > 0 && <span className="text-green-700">{it.emails.length} email</span>}
                    {it.emails.length > 0 && it.phones.length > 0 && <span className="text-gray-300"> · </span>}
                    {it.phones.length > 0 && <span className="text-blue-700">{it.phones.length} phone</span>}
                    {it.emails.length === 0 && it.phones.length === 0 && (
                      <span className="text-gray-400">none</span>
                    )}
                  </td>
                  <td className="table-td text-xs">{it.country || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data && totalPages > 1 && (
          <div className="mt-3 flex items-center justify-between text-sm">
            <button className="btn-secondary" disabled={page <= 1 || busy}
                    onClick={() => load(page - 1)}>← Previous</button>
            <span className="text-gray-500">Page {page} of {totalPages}</span>
            <button className="btn-secondary" disabled={page >= totalPages || busy}
                    onClick={() => load(page + 1)}>Next →</button>
          </div>
        )}
      </div>
    </div>
  );
}
