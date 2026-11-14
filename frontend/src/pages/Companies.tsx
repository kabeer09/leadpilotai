import { useState, useEffect, useCallback } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Search, Download } from 'lucide-react';
import { listCompanies } from '../lib/api';

const BAND_BADGE: Record<string, string> = {
  Hot: 'bg-rose-100 text-rose-700',
  Warm: 'bg-amber-100 text-amber-700',
  Nurture: 'bg-sky-100 text-sky-700',
  Low: 'bg-gray-100 text-gray-600',
};

export default function Companies() {
  const [params] = useSearchParams();
  const [items, setItems] = useState<{ id: number; name: string; country: string | null; website: string | null; emails: string[]; phones: string[]; lifecycle: string; lead_status: string; category: string | null; lead_score: { score: number; band: string } | null; has_research: boolean }[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState(params.get('q') || '');
  const [country, setCountry] = useState('');
  const [lifecycle, setLifecycle] = useState('active');
  const [hasEmail, setHasEmail] = useState('');
  const [hasResearch, setHasResearch] = useState('');
  const [band, setBand] = useState('');
  const [filterError, setFilterError] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setFilterError('');
    try {
      const p: Record<string, string | number> = { page, page_size: 25, lifecycle };
      if (search) p.search = search;
      if (country) p.country = country;
      if (hasEmail) p.has_email = hasEmail === 'yes' ? 'true' : 'false';
      if (hasResearch) p.has_research = hasResearch === 'yes' ? 'true' : 'false';
      if (band) p.band = band;
      const d = await listCompanies(p);
      setItems(d.items);
      setTotal(d.total);
    } catch (e: unknown) {
      // Typed 422s (e.g. the bounded band scan) must surface honestly —
      // never show a partial page as if it were the full result.
      const err = e as { response?: { data?: { detail?: string } } };
      setFilterError(err.response?.data?.detail || 'Failed to load companies.');
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page, search, country, lifecycle, hasEmail, hasResearch, band]);

  useEffect(() => {
    const t = setTimeout(load, 300);
    return () => clearTimeout(t);
  }, [load]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold text-gray-900">Companies ({total.toLocaleString()})</h1>
        <a href="/api/companies/export" className="btn-secondary text-sm"><Download className="mr-1 h-4 w-4" /> CSV</a>
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input className="input pl-10" placeholder="Search name…" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} />
        </div>
        <input className="input sm:w-40" placeholder="Country" value={country} onChange={(e) => { setCountry(e.target.value); setPage(1) }} />
        <select className="input sm:w-40" value={hasEmail} onChange={(e) => { setHasEmail(e.target.value); setPage(1) }}>
          <option value="">Email: any</option>
          <option value="yes">Has email</option>
          <option value="no">No email</option>
        </select>
        <select className="input sm:w-44" value={hasResearch} onChange={(e) => { setHasResearch(e.target.value); setPage(1) }}>
          <option value="">Research: any</option>
          <option value="yes">Has research</option>
          <option value="no">No research</option>
        </select>
        <select className="input sm:w-36" value={band} onChange={(e) => { setBand(e.target.value); setPage(1) }}>
          <option value="">Band: any</option>
          <option value="Hot">Hot</option>
          <option value="Warm">Warm</option>
          <option value="Nurture">Nurture</option>
          <option value="Low">Low</option>
        </select>
        <select className="input sm:w-44" value={lifecycle} onChange={(e) => { setLifecycle(e.target.value); setPage(1) }}>
          <option value="active">Active</option>
          <option value="all">All</option>
          <option value="EXCLUDED_NO_CONTACT">Excluded (no contact)</option>
          <option value="ARCHIVED">Archived</option>
          <option value="MERGED">Merged</option>
        </select>
      </div>
      {filterError && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800">
          {filterError}
        </div>
      )}
      <div className="card overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="table-th">Company</th>
                <th className="table-th">Country</th>
                <th className="table-th">Emails</th>
                <th className="table-th">Phones</th>
                <th className="table-th">Lead score</th>
                <th className="table-th">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {loading ? (
                <tr><td colSpan={6} className="table-td text-center">Loading…</td></tr>
              ) : items.length === 0 ? (
                <tr><td colSpan={6} className="table-td text-center text-gray-400">
                  {total === 0 && lifecycle === 'active' && !band && !hasResearch ? 'No companies yet — crawl a website first.' : 'No matches.'}
                </td></tr>
              ) : items.map((c) => (
                <tr key={c.id} className="hover:bg-gray-50">
                  <td className="table-td">
                    <Link to={`/companies/${c.id}`} className="font-medium text-primary-600">{c.name}</Link>
                    {c.website && <p className="text-xs text-gray-400">{c.website}</p>}
                    {!c.has_research && <p className="text-[10px] text-gray-300">no research yet</p>}
                  </td>
                  <td className="table-td">{c.country || '—'}</td>
                  <td className="table-td text-xs">{c.emails.length > 0 ? c.emails.join(', ') : <span className="text-gray-400">—</span>}</td>
                  <td className="table-td text-xs">{c.phones.length > 0 ? c.phones.join(', ') : <span className="text-gray-400">—</span>}</td>
                  <td className="table-td">
                    {c.lead_score ? (
                      <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold ${BAND_BADGE[c.lead_score.band] || 'bg-gray-100 text-gray-600'}`}>
                        {c.lead_score.band} · {c.lead_score.score}
                      </span>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                  <td className="table-td"><span className="badge bg-gray-100 text-gray-700">{c.lead_status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="flex items-center justify-between text-xs text-gray-500">
        <span>Page {page} of {Math.max(1, Math.ceil(total / 25))}</span>
        <div className="flex gap-2">
          <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)} className="btn-secondary text-xs">Prev</button>
          <button disabled={page * 25 >= total} onClick={() => setPage((p) => p + 1)} className="btn-secondary text-xs">Next</button>
        </div>
      </div>
    </div>
  );
}
