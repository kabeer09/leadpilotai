import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Save, Trash2, Download, AlertTriangle, Info,
  Building2, FlaskConical,
} from 'lucide-react';
import {
  discoveryBuyers, discoveryExport,
  discoverySavedList, discoverySavedCreate, discoverySavedDelete,
  listSources,
} from '../lib/api';

const errText = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
  (e as Error)?.message || 'Request failed';

interface Signal { type: string; keyword: string; field: string }
interface Item {
  id: number; name: string; country?: string; city?: string;
  website?: string; category?: string; hall?: string; booth?: string;
  product_summary?: string; profile_url?: string; source_id?: number;
  fair?: string; buyer_type: string; buyer_types: string[];
  role: string; roles: string[]; signals: Signal[];
  import_activity: boolean; import_signals: Signal[];
  emails: string[]; phones: string[]; contact_complete: boolean;
  lead_status: string;
}
interface Facet { value: string | number; count: number }
interface BuyersResp {
  record_type: string; disclaimer: string;
  source: { name: string; coverage?: string; classification?: string; origins?: Record<string, number> };
  facet_note?: string;
  total: number; page: number; page_size: number; scanned: number;
  facets: { buyer_types: Facet[]; roles: Facet[]; countries: Facet[]; categories: Facet[]; source_ids: Facet[] };
  type_definitions: { value: string; role: string }[];
  items: Item[];
}
interface Saved { id: number; name: string; query: Record<string, string | number | boolean> }

const ROLE_BADGE: Record<string, string> = {
  buyer: 'bg-blue-100 text-blue-700',
  supplier: 'bg-purple-100 text-purple-700',
  trading: 'bg-amber-100 text-amber-700',
  unclassified: 'bg-gray-100 text-gray-500',
};

const tri = (v: string) => (v === '' ? undefined : v === 'true');

export default function Discovery() {
  const [q, setQ] = useState('');
  const [country, setCountry] = useState('');
  const [category, setCategory] = useState('');
  const [sourceId, setSourceId] = useState('');
  const [buyerType, setBuyerType] = useState('');
  const [role, setRole] = useState('');
  const [importAct, setImportAct] = useState('');
  const [hasEmail, setHasEmail] = useState('');
  const [hasPhone, setHasPhone] = useState('');
  const [hasWebsite, setHasWebsite] = useState('');
  const [sort, setSort] = useState('name');

  const [page, setPage] = useState(1);
  const [data, setData] = useState<BuyersResp | null>(null);
  const [saved, setSaved] = useState<Saved[]>([]);
  const [fairNames, setFairNames] = useState<Record<number, string>>({});
  const [saveName, setSaveName] = useState('');
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(''), 4000); };

  const filters = useCallback(() => {
    const p: Record<string, string | number | boolean> = { page_size: 25, sort };
    if (q.trim()) p.q = q.trim();
    if (country) p.country = country;
    if (category) p.category = category;
    if (sourceId) p.source_id = Number(sourceId);
    if (buyerType) p.buyer_type = buyerType;
    if (role) p.role = role;
    if (tri(importAct) !== undefined) p.import_activity = tri(importAct)!;
    if (tri(hasEmail) !== undefined) p.has_email = tri(hasEmail)!;
    if (tri(hasPhone) !== undefined) p.has_phone = tri(hasPhone)!;
    if (tri(hasWebsite) !== undefined) p.has_website = tri(hasWebsite)!;
    return p;
  }, [q, country, category, sourceId, buyerType, role, importAct,
      hasEmail, hasPhone, hasWebsite, sort]);

  const load = useCallback(async (p?: number) => {
    setBusy(true); setErr('');
    try {
      const body = await discoveryBuyers({ ...filters(), page: p || 1 });
      setData(body); setPage(body.page);
    } catch (e) { setErr(errText(e)); }
    finally { setBusy(false); }
  }, [filters]);

  const loadSaved = useCallback(() => {
    discoverySavedList().then((d) => setSaved(d.items || [])).catch(() => {});
  }, []);

  useEffect(() => { load(1); loadSaved(); }, []); // initial
  useEffect(() => {
    const t = setTimeout(() => load(1), 350); // debounced filter changes
    return () => clearTimeout(t);
  }, [q, country, category, sourceId, buyerType, role, importAct,
      hasEmail, hasPhone, hasWebsite, sort]);
  useEffect(() => {
    listSources().then((r) => {
      const arr = Array.isArray(r) ? r : (r.items || []);
      const map: Record<number, string> = {};
      for (const s of arr) map[s.id] = s.name || s.url;
      setFairNames(map);
    }).catch(() => {});
  }, []);

  const saveCurrent = async () => {
    if (!saveName.trim()) return setErr('Give the search a name');
    try {
      await discoverySavedCreate({ name: saveName.trim(), query: filters() });
      setSaveName(''); flash('Search saved'); loadSaved();
    } catch (e) { setErr(errText(e)); }
  };

  const applySaved = (s: Saved) => {
    const g = (k: string) => (s.query[k] != null ? String(s.query[k]) : '');
    setQ(g('q')); setCountry(g('country')); setCategory(g('category'));
    setSourceId(g('source_id')); setBuyerType(g('buyer_type'));
    setRole(g('role'));
    setImportAct('import_activity' in s.query ? String(s.query.import_activity) : '');
    setHasEmail('has_email' in s.query ? String(s.query.has_email) : '');
    setHasPhone('has_phone' in s.query ? String(s.query.has_phone) : '');
    setHasWebsite('has_website' in s.query ? String(s.query.has_website) : '');
    setSort(g('sort') || 'name');
    flash(`Loaded "${s.name}"`);
  };

  const removeSaved = async (s: Saved) => {
    try { await discoverySavedDelete(s.id); flash('Deleted'); loadSaved(); }
    catch (e) { setErr(errText(e)); }
  };

  const download = async () => {
    setErr('');
    try {
      const blob = await discoveryExport(filters());
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `buyer-discovery-${Date.now()}.csv`;
      a.click(); URL.revokeObjectURL(url);
      flash('Downloaded CSV');
    } catch (e) { setErr(errText(e)); }
  };

  const facets = data?.facets;
  const page_size = 25;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / page_size)) : 1;

  return (
    <div className="space-y-5 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Buyer Discovery</h1>
          <p className="text-sm text-gray-500">
            Classify and filter the companies already in your workspace —
            importers, distributors, retailers, manufacturers & more
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

      {/* honesty banner */}
      <div className="flex items-start gap-2 rounded-2xl border border-amber-100 bg-amber-50 px-4 py-3 text-sm text-amber-900">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <span>
          <b>Keyword-signal classification.</b> {data?.disclaimer ||
            'Presence in a directory or trade fair is NOT evidence of active buying activity — verify before outreach.'}
          {data?.source?.coverage && (
            <span className="ml-2 text-amber-700">Coverage: {data.source.coverage}.</span>
          )}
        </span>
      </div>

      {/* filters */}
      <div className="card grid gap-3 md:grid-cols-3 lg:grid-cols-4">
        <div className="lg:col-span-2">
          <label className="label">Keyword (name / products / address)</label>
          <input className="input mt-1" value={q} placeholder="e.g. copper, wholesale, gift…"
                 onChange={(e) => setQ(e.target.value)} />
        </div>
        <div>
          <label className="label">Country</label>
          <select className="input mt-1" value={country} onChange={(e) => setCountry(e.target.value)}>
            <option value="">All countries</option>
            {(facets?.countries || []).map((f) => (
              <option key={f.value} value={f.value}>{f.value} ({f.count})</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Fair / source</label>
          <select className="input mt-1" value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
            <option value="">All sources</option>
            {(facets?.source_ids || []).map((f) => (
              <option key={f.value} value={f.value}>
                {fairNames[Number(f.value)] || `Source #${f.value}`} ({f.count})
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Buyer type</label>
          <select className="input mt-1" value={buyerType} onChange={(e) => setBuyerType(e.target.value)}>
            <option value="">All types</option>
            {(facets?.buyer_types || []).map((f) => (
              <option key={f.value} value={f.value}>{f.value} ({f.count})</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Role</label>
          <select className="input mt-1" value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="">All roles</option>
            {(facets?.roles || []).map((f) => (
              <option key={f.value} value={f.value}>
                {f.value} ({f.count})
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Import/export signal</label>
          <select className="input mt-1" value={importAct} onChange={(e) => setImportAct(e.target.value)}>
            <option value="">Any</option>
            <option value="true">Mentioned in text</option>
            <option value="false">Not mentioned</option>
          </select>
        </div>
        <div>
          <label className="label">Sort</label>
          <select className="input mt-1" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="name">Name</option>
            <option value="country">Country</option>
            <option value="type">Buyer type</option>
            <option value="updated">Last updated</option>
          </select>
        </div>
        <div>
          <label className="label">Has email</label>
          <select className="input mt-1" value={hasEmail} onChange={(e) => setHasEmail(e.target.value)}>
            <option value="">Any</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </div>
        <div>
          <label className="label">Has phone</label>
          <select className="input mt-1" value={hasPhone} onChange={(e) => setHasPhone(e.target.value)}>
            <option value="">Any</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </div>
        <div>
          <label className="label">Has website</label>
          <select className="input mt-1" value={hasWebsite} onChange={(e) => setHasWebsite(e.target.value)}>
            <option value="">Any</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </div>
        <div className="flex items-end gap-2">
          <input className="input flex-1" placeholder="Name to save…"
                 value={saveName} onChange={(e) => setSaveName(e.target.value)} />
          <button className="btn-lime" onClick={saveCurrent}>
            <Save className="mr-1 h-4 w-4" /> Save
          </button>
        </div>
      </div>

      {saved.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {saved.map((s) => (
            <div key={s.id}
                 className="flex items-center gap-1.5 rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-xs">
              <button className="font-medium text-gray-700 hover:text-black"
                      onClick={() => applySaved(s)}>{s.name}</button>
              <button className="text-gray-400 hover:text-red-600"
                      onClick={() => removeSaved(s)} title="Delete">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* results */}
      <div className="card overflow-x-auto">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-gray-700">
            Results {data ? `(${data.total} companies)` : ''}
            {busy && <span className="ml-2 font-normal text-gray-400">loading…</span>}
          </h2>
          {facets && (
            <span className="text-xs text-gray-400">
              {facets.buyer_types.filter((f) => f.count > 0).length} types matched ·
              multi-label signals
            </span>
          )}
        </div>
        {data && data.total === 0 && (
          <p className="py-8 text-center text-sm text-gray-400">
            No companies match these filters in your workspace.
          </p>
        )}
        {data && data.items.length > 0 && (
          <table className="min-w-full">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="table-th">Company</th>
                <th className="table-th">Country</th>
                <th className="table-th">Buyer type</th>
                <th className="table-th">Role</th>
                <th className="table-th">Import signal</th>
                <th className="table-th">Fair / source</th>
                <th className="table-th">Contacts</th>
                <th className="table-th">Actions</th>
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
                    {it.category && (
                      <p className="truncate text-xs text-gray-400 max-w-[200px]">{it.category}</p>
                    )}
                  </td>
                  <td className="table-td">{it.country || '—'}</td>
                  <td className="table-td">
                    <span className={`badge ${ROLE_BADGE[it.role] || 'bg-gray-100 text-gray-500'}`}
                          title={it.signals.map((s) => `${s.keyword} → ${s.field}`).join('\n') || 'no signal matched'}>
                      {it.buyer_type}
                    </span>
                    {it.buyer_types.length > 1 && (
                      <span className="ml-1 text-[10px] text-gray-400">+{it.buyer_types.length - 1}</span>
                    )}
                  </td>
                  <td className="table-td text-xs capitalize">{it.role}</td>
                  <td className="table-td">
                    {it.import_activity ? (
                      <span className="badge bg-emerald-100 text-emerald-700"
                            title={it.import_signals.map((s) => `${s.keyword} → ${s.field}`).join('\n')}>
                        mentioned
                      </span>
                    ) : (
                      <span className="text-xs text-gray-400">—</span>
                    )}
                  </td>
                  <td className="table-td max-w-[160px] truncate text-xs text-gray-500"
                      title={it.fair || ''}>
                    {it.fair || (it.source_id ? `Source #${it.source_id}` : '—')}
                  </td>
                  <td className="table-td text-xs">
                    {it.emails.length > 0 && <span className="text-green-700">{it.emails.length} email</span>}
                    {it.emails.length > 0 && it.phones.length > 0 && <span className="text-gray-300"> · </span>}
                    {it.phones.length > 0 && <span className="text-blue-700">{it.phones.length} phone</span>}
                    {!it.contact_complete && <span className="text-gray-400">none</span>}
                  </td>
                  <td className="table-td">
                    <div className="flex gap-2 text-xs">
                      <Link to={`/research?company=${it.id}`}
                            className="flex items-center gap-1 font-medium text-primary-700 hover:underline">
                        <FlaskConical className="h-3.5 w-3.5" /> Research
                      </Link>
                      {it.website && (
                        <a href={it.website} target="_blank" rel="noreferrer"
                           className="flex items-center gap-1 text-gray-500 hover:underline">
                          <Building2 className="h-3.5 w-3.5" /> Site
                        </a>
                      )}
                    </div>
                  </td>
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
