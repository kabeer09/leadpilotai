import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { Search, Download } from 'lucide-react';
import { listCompanies } from '../lib/api';

export default function Companies() {
  const [items, setItems] = useState<{ id: number; name: string; country: string | null; website: string | null; emails: string[]; phones: string[]; lifecycle: string; lead_status: string; category: string | null }[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [country, setCountry] = useState('');
  const [lifecycle, setLifecycle] = useState('active');
  const [hasEmail, setHasEmail] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const p: Record<string, string | number> = { page, page_size: 25, lifecycle };
      if (search) p.search = search;
      if (country) p.country = country;
      if (hasEmail) p.has_email = hasEmail === 'yes' ? 'true' : 'false';
      const d = await listCompanies(p);
      setItems(d.items);
      setTotal(d.total);
    } finally {
      setLoading(false);
    }
  }, [page, search, country, lifecycle, hasEmail]);

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
        <select className="input sm:w-44" value={lifecycle} onChange={(e) => { setLifecycle(e.target.value); setPage(1) }}>
          <option value="active">Active</option>
          <option value="all">All</option>
          <option value="EXCLUDED_NO_CONTACT">Excluded (no contact)</option>
          <option value="ARCHIVED">Archived</option>
          <option value="MERGED">Merged</option>
        </select>
      </div>
      <div className="card overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="table-th">Company</th>
                <th className="table-th">Country</th>
                <th className="table-th">Emails</th>
                <th className="table-th">Phones</th>
                <th className="table-th">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {loading ? (
                <tr><td colSpan={5} className="table-td text-center">Loading…</td></tr>
              ) : items.length === 0 ? (
                <tr><td colSpan={5} className="table-td text-center text-gray-400">
                  {total === 0 && lifecycle === 'active' ? 'No companies yet — crawl a website first.' : 'No matches.'}
                </td></tr>
              ) : items.map((c) => (
                <tr key={c.id} className="hover:bg-gray-50">
                  <td className="table-td">
                    <Link to={`/companies/${c.id}`} className="font-medium text-primary-600">{c.name}</Link>
                    {c.website && <p className="text-xs text-gray-400">{c.website}</p>}
                  </td>
                  <td className="table-td">{c.country || '—'}</td>
                  <td className="table-td text-xs">{c.emails.length > 0 ? c.emails.join(', ') : <span className="text-gray-400">—</span>}</td>
                  <td className="table-td text-xs">{c.phones.length > 0 ? c.phones.join(', ') : <span className="text-gray-400">—</span>}</td>
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
