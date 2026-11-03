import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import {
  Search, Globe, MapPin, ExternalLink, Play, Pause, RotateCcw,
  Square, Download, RefreshCw, Building2, Star,
} from 'lucide-react';
import {
  gbCreateCampaign, gbListCampaigns, gbCancelCampaign, gbPauseCampaign,
  gbResumeCampaign, gbListLeads, gbUpdateLead, gbEnrich, gbListJobs,
  gbCancelJob, gbRetryJob, gbSettings, gbExport,
} from '../lib/api';

// ---------------------------------------------------------------- types
type Campaign = {
  id: number; campaign_name: string; keywords: string[]; categories: string[];
  business_types: string[]; countries: string[]; state: string | null;
  cities: string[]; max_results: number; status: string;
  total_discovered: number; total_enriched: number; total_skipped: number;
  duplicate_count: number; api_calls: number; api_errors: number;
  estimated_cost_usd: number; error: string | null; running: boolean;
  created_at: string; finished_at: string | null;
  quota_calls_used_today: number; quota_calls_daily: number;
};
type GBLead = {
  id: number; company_name: string; business_category: string | null;
  business_type: string; country: string | null; state: string | null;
  city: string | null; website: string | null; phone: string | null;
  email: string | null; rating: number | null; review_count: number | null;
  relevance_score: number; relevance_label: string | null;
  enrichment_status: string; lead_status: string; google_maps_url: string | null;
  campaign_id: number | null;
};
type GBJob = {
  id: number; campaign_id: number | null; status: string; total: number;
  processed: number; enriched: number; skipped: number; errors: number;
  attempts: number; error: string | null; running: boolean;
  finished_at: string | null;
};
type GBCfg = {
  google_places: { configured: boolean; max_results_per_search: number;
    cost_per_call_usd: number; calls_used_today: number;
    daily_call_budget: number };
  enrichment: { enabled: boolean; max_pages_per_site: number;
    request_delay_seconds: number };
  usage: { campaigns: number; leads: number; enriched: number;
    api_calls_total: number; estimated_cost_total_usd: number };
  note: string;
};

const COUNTRIES = [
  'Australia', 'Austria', 'Belgium', 'Canada', 'Denmark', 'Finland', 'France',
  'Germany', 'Greece', 'India', 'Ireland', 'Italy', 'Japan', 'Malaysia',
  'Netherlands', 'New Zealand', 'Norway', 'Poland', 'Portugal', 'Singapore',
  'South Korea', 'Spain', 'Sweden', 'Switzerland', 'Turkey', 'UAE',
  'United Kingdom', 'United States',
];

const splitList = (s: string) =>
  s.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);

const statusBadge = (s: string) => {
  switch (s) {
    case 'RUNNING': return 'bg-blue-100 text-blue-700 animate-pulse';
    case 'PENDING': return 'bg-gray-100 text-gray-700';
    case 'PAUSED': return 'bg-amber-100 text-amber-700';
    case 'COMPLETED': return 'bg-green-100 text-green-700';
    case 'FAILED': return 'bg-red-100 text-red-700';
    case 'CANCELLED': return 'bg-gray-200 text-gray-500';
    default: return 'bg-gray-100 text-gray-700';
  }
};
const relBadge = (label: string | null) => {
  switch (label) {
    case 'Highly Relevant': return 'bg-lime-100 text-lime-800';
    case 'Relevant': return 'bg-green-100 text-green-700';
    case 'Potential Match': return 'bg-amber-100 text-amber-700';
    default: return 'bg-gray-100 text-gray-600';
  }
};
const enrBadge = (s: string) => {
  switch (s) {
    case 'ENRICHED': return 'bg-green-100 text-green-700';
    case 'NOT_ENRICHED': return 'bg-gray-100 text-gray-600';
    case 'NO_WEBSITE': return 'bg-amber-100 text-amber-700';
    default: return 'bg-red-100 text-red-700';
  }
};

const errText = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
  (e as Error)?.message || 'Request failed';

export default function GBLeads() {
  // ---- search form ----
  const [name, setName] = useState('');
  const [keywords, setKeywords] = useState('');
  const [categories, setCategories] = useState('');
  const [btypes, setBtypes] = useState('');
  const [country, setCountry] = useState('');
  const [state_, setState_] = useState('');
  const [cities, setCities] = useState('');
  const [maxResults, setMaxResults] = useState(20);
  const [fWebsite, setFWebsite] = useState(false);
  const [fPhone, setFPhone] = useState(false);
  const [fRating, setFRating] = useState('');
  const [fReviews, setFReviews] = useState('');
  const [enrichAfter, setEnrichAfter] = useState(true);

  // ---- data ----
  const [cfg, setCfg] = useState<GBCfg | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [jobs, setJobs] = useState<GBJob[]>([]);
  const [leads, setLeads] = useState<GBLead[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const pageSize = 25;

  // ---- lead filters ----
  const [lf, setLf] = useState({
    q: '', country: '', city: '', business_type: '', lead_status: '',
    enrichment_status: '', min_relevance: '',
    has_email: false, has_website: false, has_phone: false,
    campaign_id: 0, sort: 'relevance', order: 'desc',
  });

  const [selected, setSelected] = useState<number[]>([]);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(''), 4000); };

  // ---- loads ----
  const loadCfg = useCallback(() => {
    gbSettings().then(setCfg).catch(() => {});
  }, []);
  const loadCampaigns = useCallback(() => {
    gbListCampaigns().then((d) => setCampaigns(d.campaigns || [])).catch(() => {});
  }, []);
  const loadJobs = useCallback(() => {
    gbListJobs().then((d) => setJobs(d.jobs || [])).catch(() => {});
  }, []);

  const leadParams = useCallback(() => {
    const p: Record<string, string | number | boolean> = { page, page_size: pageSize };
    if (lf.q) p.q = lf.q;
    if (lf.country) p.country = lf.country;
    if (lf.city) p.city = lf.city;
    if (lf.business_type) p.business_type = lf.business_type;
    if (lf.lead_status) p.lead_status = lf.lead_status;
    if (lf.enrichment_status) p.enrichment_status = lf.enrichment_status;
    if (lf.min_relevance) p.min_relevance = Number(lf.min_relevance);
    if (lf.has_email) p.has_email = true;
    if (lf.has_website) p.has_website = true;
    if (lf.has_phone) p.has_phone = true;
    if (lf.campaign_id) p.campaign_id = lf.campaign_id;
    p.sort = lf.sort; p.order = lf.order;
    return p;
  }, [lf, page]);

  const loadLeads = useCallback(() => {
    gbListLeads(leadParams())
      .then((d) => { setLeads(d.items || []); setTotal(d.total || 0); })
      .catch((e) => setErr(errText(e)));
  }, [leadParams]);

  useEffect(() => {
    loadCfg(); loadCampaigns(); loadJobs(); loadLeads();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- polling while work is active ----
  const active =
    campaigns.some((c) => c.status === 'PENDING' || c.status === 'RUNNING') ||
    jobs.some((j) => j.status === 'PENDING' || j.status === 'RUNNING');
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => {
      loadCampaigns(); loadJobs(); loadLeads();
    }, 4000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, loadCampaigns, loadJobs, loadLeads]);

  // ---- actions ----
  const submitSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr('');
    const kws = splitList(keywords);
    if (!name.trim()) return setErr('Campaign name is required');
    if (!kws.length) return setErr('Add at least one search keyword');
    setBusy(true);
    try {
      const filters: Record<string, unknown> = {};
      if (fWebsite) filters.has_website = true;
      if (fPhone) filters.has_phone = true;
      if (fRating) filters.min_rating = Number(fRating);
      if (fReviews) filters.min_reviews = Number(fReviews);
      await gbCreateCampaign({
        campaign_name: name.trim(),
        keywords: kws,
        categories: splitList(categories),
        business_types: splitList(btypes),
        countries: country ? [country] : [],
        state: state_.trim() || null,
        cities: splitList(cities),
        max_results: maxResults,
        filters,
        enrich: enrichAfter,
        start: true,
      });
      flash('Search started — watch the progress below.');
      setName('');
      loadCampaigns(); loadCfg();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const campAction = async (id: number, fn: (i: number) => Promise<unknown>) => {
    setErr('');
    try { await fn(id); loadCampaigns(); }
    catch (e) { setErr(errText(e)); }
  };

  const doEnrich = async (ids: number[]) => {
    if (!ids.length) return;
    setErr(''); setBusy(true);
    try {
      await gbEnrich({ lead_ids: ids });
      flash(`Enrichment queued for ${ids.length} lead(s).`);
      setSelected([]);
      loadJobs();
    } catch (e) { setErr(errText(e)); }
    finally { setBusy(false); }
  };

  const toggleSave = async (l: GBLead) => {
    try {
      await gbUpdateLead(l.id, {
        lead_status: l.lead_status === 'SAVED' ? 'NEW' : 'SAVED',
      });
      loadLeads();
    } catch (e) { setErr(errText(e)); }
  };

  const doExport = async (format: 'csv' | 'xlsx') => {
    setErr(''); setBusy(true);
    try {
      const blob = await gbExport({ ...leadParams(), format, page: 1, page_size: 1 });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `gb-leads-${new Date().toISOString().slice(0, 10)}.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      flash(`Exported ${total} lead(s) as ${format.toUpperCase()}.`);
    } catch (e) { setErr(errText(e)); }
    finally { setBusy(false); }
  };

  const setFilter = (patch: Partial<typeof lf>) => {
    setLf((f) => ({ ...f, ...patch }));
    setPage(1);
    setSelected([]);
  };
  const toggleSort = (col: string) => {
    setLf((f) => ({
      ...f,
      sort: col,
      order: f.sort === col && f.order === 'desc' ? 'asc' : 'desc',
    }));
    setPage(1);
  };
  const sortArrow = (col: string) =>
    lf.sort === col ? (lf.order === 'desc' ? ' ↓' : ' ↑') : '';

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const notConfigured = cfg && !cfg.google_places.configured;

  // ---------------------------------------------------------------- render
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-gray-900">
            <Building2 className="h-6 w-6" /> Google Business Lead Finder
          </h1>
          <p className="text-sm text-gray-500">
            Discover businesses by keyword and location via Google Places API (New),
            then enrich their official websites for published contact details.
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <span>
            {cfg
              ? `${cfg.google_places.calls_used_today}/${cfg.google_places.daily_call_budget} Google calls today`
              : '…'}
          </span>
          <button className="btn-secondary text-sm" onClick={() => { loadCfg(); loadCampaigns(); loadJobs(); loadLeads(); }}>
            <RefreshCw className="mr-1 h-4 w-4" /> Refresh
          </button>
        </div>
      </div>

      {notConfigured && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <strong>Google Places API not configured.</strong> Add{' '}
          <code className="rounded bg-amber-100 px-1">GOOGLE_MAPS_API_KEY</code> to{' '}
          <code className="rounded bg-amber-100 px-1">backend/.env</code>, enable
          “Places API (New)” and “Place Details (New)” in Google Cloud, then restart
          the backend. Searches stay disabled until then.
        </div>
      )}
      {err && <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">{err}</div>}
      {msg && <div className="rounded-xl border border-green-200 bg-green-50 px-4 py-2 text-sm text-green-700">{msg}</div>}

      {/* ---------------- search form ---------------- */}
      <form onSubmit={submitSearch} className="card space-y-4">
        <h2 className="flex items-center gap-2 font-semibold">
          <Search className="h-4 w-4" /> New search campaign
        </h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
          <label className="block text-sm">
            <span className="label">Campaign name *</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)}
              placeholder="Australia Copper Bottle Buyers" />
          </label>
          <label className="block text-sm md:col-span-2">
            <span className="label">Search keywords * (one per line or comma-separated)</span>
            <textarea className="input" rows={3} value={keywords}
              onChange={(e) => setKeywords(e.target.value)}
              placeholder={'Copper bottles\nCopper water bottles\nCopper drinkware'} />
          </label>
          <label className="block text-sm">
            <span className="label">Product categories</span>
            <input className="input" value={categories} onChange={(e) => setCategories(e.target.value)}
              placeholder="Home decor, Drinkware" />
          </label>
          <label className="block text-sm">
            <span className="label">Business type</span>
            <input className="input" value={btypes} onChange={(e) => setBtypes(e.target.value)}
              placeholder="Manufacturer, Wholesaler, Importer" />
          </label>
          <label className="block text-sm">
            <span className="label">Country</span>
            <input className="input" list="gb-countries" value={country}
              onChange={(e) => setCountry(e.target.value)} placeholder="Australia" />
            <datalist id="gb-countries">
              {COUNTRIES.map((c) => <option key={c} value={c} />)}
            </datalist>
          </label>
          <label className="block text-sm">
            <span className="label">State / Province</span>
            <input className="input" value={state_} onChange={(e) => setState_(e.target.value)}
              placeholder="New South Wales (optional)" />
          </label>
          <label className="block text-sm">
            <span className="label">Cities (comma-separated — empty = whole country)</span>
            <input className="input" value={cities} onChange={(e) => setCities(e.target.value)}
              placeholder="Sydney, Melbourne, Brisbane" />
          </label>
          <label className="block text-sm">
            <span className="label">Maximum results (1–{cfg?.google_places.max_results_per_search || 60})</span>
            <input className="input" type="number" min={1} max={cfg?.google_places.max_results_per_search || 60}
              value={maxResults} onChange={(e) => setMaxResults(Number(e.target.value))} />
          </label>
        </div>

        <details className="rounded-xl border border-gray-200 px-3 py-2">
          <summary className="cursor-pointer text-sm font-medium text-gray-700">
            Additional filters
          </summary>
          <div className="mt-3 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={fWebsite} onChange={(e) => setFWebsite(e.target.checked)} />
              With website
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={fPhone} onChange={(e) => setFPhone(e.target.checked)} />
              With phone
            </label>
            <label className="block">
              <span className="text-gray-600">Min rating</span>
              <input className="input mt-1" type="number" step="0.1" min={0} max={5}
                value={fRating} onChange={(e) => setFRating(e.target.value)} placeholder="4.0" />
            </label>
            <label className="block">
              <span className="text-gray-600">Min reviews</span>
              <input className="input mt-1" type="number" min={0}
                value={fReviews} onChange={(e) => setFReviews(e.target.value)} placeholder="10" />
            </label>
            <label className="col-span-2 flex items-center gap-2">
              <input type="checkbox" checked={enrichAfter}
                onChange={(e) => setEnrichAfter(e.target.checked)} />
              Enrich official websites after discovery (extract published contacts)
            </label>
          </div>
        </details>

        <div className="flex items-center gap-3">
          <button className="btn-lime" type="submit" disabled={busy || !!notConfigured}>
            <Play className="mr-1 h-4 w-4" />
            {busy ? 'Starting…' : 'Run search'}
          </button>
          <span className="text-xs text-gray-500">
            ≈{cfg?.google_places.cost_per_call_usd ?? 0.032} USD per Google request ·
            daily budget {cfg?.google_places.daily_call_budget ?? '…'} calls
          </span>
        </div>
      </form>

      {/* ---------------- campaigns ---------------- */}
      <div className="card">
        <h2 className="font-semibold">Search campaigns</h2>
        {campaigns.length === 0 && (
          <p className="mt-2 text-sm italic text-gray-400">No campaigns yet — run your first search above.</p>
        )}
        <div className="mt-3 space-y-3">
          {campaigns.map((c) => {
            const pct = c.max_results
              ? Math.min(100, Math.round((c.total_discovered / c.max_results) * 100))
              : 0;
            return (
              <div key={c.id} className="rounded-xl border border-gray-200 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`badge ${statusBadge(c.status)}`}>{c.status}</span>
                  <span className="font-medium text-gray-900">{c.campaign_name}</span>
                  <span className="text-xs text-gray-500">
                    {c.keywords.join(', ')}
                    {(c.countries.length || c.cities.length) && (
                      <> · {c.cities.length ? c.cities.join(', ') : c.countries.join(', ')}{c.state ? `, ${c.state}` : ''}</>
                    )}
                  </span>
                  <span className="ml-auto flex items-center gap-1">
                    {c.status === 'RUNNING' && (
                      <button className="btn-secondary px-2 py-1 text-xs"
                        onClick={() => campAction(c.id, gbPauseCampaign)}>
                        <Pause className="h-3 w-3" /> Pause
                      </button>
                    )}
                    {(c.status === 'PAUSED' || c.status === 'FAILED') && (
                      <button className="btn-secondary px-2 py-1 text-xs"
                        onClick={() => campAction(c.id, gbResumeCampaign)}>
                        <RotateCcw className="h-3 w-3" /> {c.status === 'FAILED' ? 'Retry' : 'Resume'}
                      </button>
                    )}
                    {(c.status === 'PENDING' || c.status === 'RUNNING' || c.status === 'PAUSED') && (
                      <button className="btn-secondary px-2 py-1 text-xs"
                        onClick={() => campAction(c.id, gbCancelCampaign)}>
                        <Square className="h-3 w-3" /> Cancel
                      </button>
                    )}
                    <button className="btn-secondary px-2 py-1 text-xs"
                      onClick={() => { setFilter({ campaign_id: c.id }); }}>
                      View leads
                    </button>
                  </span>
                </div>
                <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-gray-100">
                  <div className={`h-2 rounded-full ${c.status === 'FAILED' ? 'bg-red-400' : 'bg-lime-400'}`}
                    style={{ width: `${pct}%` }} />
                </div>
                <div className="mt-1.5 flex flex-wrap gap-x-4 text-xs text-gray-500">
                  <span>{c.total_discovered} discovered / {c.max_results} target</span>
                  <span>{c.total_enriched} enriched</span>
                  <span>{c.duplicate_count} duplicates skipped</span>
                  <span>{c.total_skipped} filtered out</span>
                  <span>{c.api_calls} Google calls ({c.api_errors} errors)</span>
                  <span>${(c.estimated_cost_usd || 0).toFixed(3)} est.</span>
                </div>
                {c.error && (
                  <p className="mt-1 break-words text-xs text-red-600">{c.error}</p>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* ---------------- enrichment jobs ---------------- */}
      {jobs.length > 0 && (
        <div className="card">
          <h2 className="font-semibold">Website enrichment jobs</h2>
          <div className="mt-3 space-y-2">
            {jobs.slice(0, 6).map((j) => (
              <div key={j.id}
                className="flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm">
                <span className={`badge ${statusBadge(j.status)}`}>{j.status}</span>
                <span>Job #{j.id}</span>
                {j.campaign_id && <span className="text-gray-500">campaign #{j.campaign_id}</span>}
                <span className="text-gray-500">
                  {j.processed}/{j.total || '…'} processed · {j.enriched} enriched ·
                  {' '}{j.skipped} skipped · {j.errors} errors
                  {j.attempts > 1 && <> · attempt {j.attempts}</>}
                </span>
                <span className="ml-auto flex gap-1">
                  {(j.status === 'PENDING' || j.status === 'RUNNING') && (
                    <button className="btn-secondary px-2 py-1 text-xs"
                      onClick={() => gbCancelJob(j.id).then(loadJobs).catch((e) => setErr(errText(e)))}>
                      Cancel
                    </button>
                  )}
                  {j.status === 'FAILED' && (
                    <button className="btn-secondary px-2 py-1 text-xs"
                      onClick={() => gbRetryJob(j.id).then(loadJobs).catch((e) => setErr(errText(e)))}>
                      <RotateCcw className="mr-1 h-3 w-3" /> Retry failed
                    </button>
                  )}
                </span>
                {j.error && <span className="w-full text-xs text-red-600">{j.error}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ---------------- results ---------------- */}
      <div className="card">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-semibold">Business leads ({total})</h2>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <button className="btn-secondary text-sm" disabled={busy || !total}
              onClick={() => doEnrich(selected.length ? selected : leads.map((l) => l.id))}>
              <Globe className="mr-1 h-4 w-4" />
              Enrich {selected.length ? `${selected.length} selected` : 'page'}
            </button>
            <button className="btn-secondary text-sm" disabled={busy || !total}
              onClick={() => doExport('csv')}>
              <Download className="mr-1 h-4 w-4" /> CSV
            </button>
            <button className="btn-secondary text-sm" disabled={busy || !total}
              onClick={() => doExport('xlsx')}>
              <Download className="mr-1 h-4 w-4" /> Excel
            </button>
          </div>
        </div>

        {/* filters */}
        <div className="mt-3 grid grid-cols-2 gap-2 text-sm md:grid-cols-4 lg:grid-cols-6">
          <input className="input col-span-2" placeholder="Search name / city…" value={lf.q}
            onChange={(e) => setFilter({ q: e.target.value })} />
          <input className="input" placeholder="Country" value={lf.country}
            onChange={(e) => setFilter({ country: e.target.value })} />
          <input className="input" placeholder="City" value={lf.city}
            onChange={(e) => setFilter({ city: e.target.value })} />
          <select className="input" value={lf.business_type}
            onChange={(e) => setFilter({ business_type: e.target.value })}>
            <option value="">Any business type</option>
            {['Manufacturer', 'Importer', 'Wholesaler', 'Retailer', 'Distributor',
              'Exporter', 'Service Provider', 'Unknown'].map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <select className="input" value={lf.enrichment_status}
            onChange={(e) => setFilter({ enrichment_status: e.target.value })}>
            <option value="">Any enrichment</option>
            <option value="NOT_ENRICHED">Not enriched</option>
            <option value="ENRICHED">Enriched</option>
            <option value="NO_WEBSITE">No website</option>
            <option value="FAILED">Failed</option>
          </select>
          <select className="input" value={lf.lead_status}
            onChange={(e) => setFilter({ lead_status: e.target.value })}>
            <option value="">Any status</option>
            <option value="NEW">New</option>
            <option value="SAVED">Saved</option>
            <option value="CONTACTED">Contacted</option>
            <option value="ARCHIVED">Archived</option>
          </select>
          <input className="input" type="number" min={0} max={100} placeholder="Min relevance"
            value={lf.min_relevance}
            onChange={(e) => setFilter({ min_relevance: e.target.value })} />
          <label className="flex items-center gap-1.5 text-xs">
            <input type="checkbox" checked={lf.has_email}
              onChange={(e) => setFilter({ has_email: e.target.checked })} /> Email
          </label>
          <label className="flex items-center gap-1.5 text-xs">
            <input type="checkbox" checked={lf.has_website}
              onChange={(e) => setFilter({ has_website: e.target.checked })} /> Website
          </label>
          <label className="flex items-center gap-1.5 text-xs">
            <input type="checkbox" checked={lf.has_phone}
              onChange={(e) => setFilter({ has_phone: e.target.checked })} /> Phone
          </label>
          {lf.campaign_id !== 0 && (
            <button className="btn-secondary text-xs"
              onClick={() => setFilter({ campaign_id: 0 })}>
              Clear campaign #{lf.campaign_id} ✕
            </button>
          )}
        </div>

        {/* table */}
        <div className="mt-3 overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-left text-xs uppercase tracking-wide text-gray-500">
                <th className="table-th w-8">
                  <input type="checkbox"
                    checked={leads.length > 0 && selected.length === leads.length}
                    onChange={(e) =>
                      setSelected(e.target.checked ? leads.map((l) => l.id) : [])} />
                </th>
                <th className="table-th cursor-pointer" onClick={() => toggleSort('name')}>
                  Company{sortArrow('name')}
                </th>
                <th className="table-th">Category</th>
                <th className="table-th">Location</th>
                <th className="table-th">Contact</th>
                <th className="table-th cursor-pointer" onClick={() => toggleSort('rating')}>
                  Rating{sortArrow('rating')}
                </th>
                <th className="table-th cursor-pointer" onClick={() => toggleSort('relevance')}>
                  Relevance{sortArrow('relevance')}
                </th>
                <th className="table-th">Type</th>
                <th className="table-th">Enrichment</th>
                <th className="table-th">Actions</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((l) => (
                <tr key={l.id} className="border-b border-gray-100 hover:bg-gray-50">
                  <td className="table-td">
                    <input type="checkbox"
                      checked={selected.includes(l.id)}
                      onChange={(e) =>
                        setSelected((s) => e.target.checked
                          ? [...s, l.id] : s.filter((i) => i !== l.id))} />
                  </td>
                  <td className="table-td max-w-[220px]">
                    <Link to={`/business-leads/${l.id}`}
                      className="font-medium text-gray-900 hover:text-primary-600">
                      {l.company_name}
                    </Link>
                    {l.lead_status === 'SAVED' && (
                      <span className="badge ml-1 bg-lime-100 text-lime-800">saved</span>
                    )}
                  </td>
                  <td className="table-td max-w-[160px] truncate text-gray-600">
                    {l.business_category || '—'}
                  </td>
                  <td className="table-td whitespace-nowrap text-gray-600">
                    {[l.city, l.country].filter(Boolean).join(', ') || '—'}
                  </td>
                  <td className="table-td max-w-[200px]">
                    {l.email && (
                      <a href={`mailto:${l.email}`}
                        className="block truncate text-xs text-primary-600">{l.email}</a>
                    )}
                    {l.phone && <span className="block text-xs text-gray-600">{l.phone}</span>}
                    {!l.email && !l.phone && <span className="text-gray-300">—</span>}
                  </td>
                  <td className="table-td whitespace-nowrap text-gray-600">
                    {l.rating != null ? (
                      <span className="inline-flex items-center gap-0.5">
                        <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" />
                        {l.rating.toFixed(1)}
                        <span className="text-xs text-gray-400">({l.review_count ?? 0})</span>
                      </span>
                    ) : '—'}
                  </td>
                  <td className="table-td whitespace-nowrap">
                    <span className={`badge ${relBadge(l.relevance_label)}`}>
                      {l.relevance_score}
                    </span>
                    <span className="ml-1 text-xs text-gray-500">{l.relevance_label}</span>
                  </td>
                  <td className="table-td whitespace-nowrap text-xs text-gray-600">
                    {l.business_type}
                  </td>
                  <td className="table-td whitespace-nowrap">
                    <span className={`badge ${enrBadge(l.enrichment_status)}`}>
                      {l.enrichment_status.replace('_', ' ').toLowerCase()}
                    </span>
                  </td>
                  <td className="table-td whitespace-nowrap">
                    <span className="flex items-center gap-1.5 text-xs">
                      <Link to={`/business-leads/${l.id}`} className="text-primary-600 hover:underline">
                        View
                      </Link>
                      {l.website && (
                        <a href={l.website} target="_blank" rel="noreferrer"
                          className="text-gray-500 hover:text-gray-700" title="Open website">
                          <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                      )}
                      {l.google_maps_url && (
                        <a href={l.google_maps_url} target="_blank" rel="noreferrer"
                          className="text-gray-500 hover:text-gray-700" title="Open Google Maps">
                          <MapPin className="h-3.5 w-3.5" />
                        </a>
                      )}
                      <button onClick={() => toggleSave(l)}
                        className="text-gray-500 hover:text-gray-700" title="Save lead">
                        {l.lead_status === 'SAVED' ? '★' : '☆'}
                      </button>
                      {l.website && l.enrichment_status !== 'ENRICHED' && (
                        <button onClick={() => doEnrich([l.id])}
                          className="text-gray-500 hover:text-gray-700" title="Enrich website">
                          <Globe className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {leads.length === 0 && (
            <p className="py-6 text-center text-sm italic text-gray-400">
              No leads match — run a search above to discover businesses.
            </p>
          )}
        </div>

        {totalPages > 1 && (
          <div className="mt-3 flex items-center justify-between text-sm">
            <span className="text-gray-500">
              Page {page} of {totalPages} · {total} total
            </span>
            <span className="flex gap-2">
              <button className="btn-secondary text-sm" disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}>Previous</button>
              <button className="btn-secondary text-sm" disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}>Next</button>
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
