import { useState, useEffect } from 'react';
import { Play, X, RefreshCw } from 'lucide-react';
import { createCrawl, listCrawls, getCrawl, cancelCrawl } from '../lib/api';

interface Job {
  id: number; url: string; status: string; pages_visited: number;
  companies_found: number; contacts_found: number; errors: string[];
}

export default function Crawling() {
  const [url, setUrl] = useState('');
  const [jobs, setJobs] = useState<Job[]>([]);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [msg, setMsg] = useState('');

  const load = async () => {
    try {
      setJobs(await listCrawls());
    } catch {}
  };
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  const start = async () => {
    setMsg('');
    try {
      const j = await createCrawl({ url });
      setMsg(`Crawl #${j.id} started.`);
      setUrl('');
      load();
    } catch (e: unknown) {
      const d = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      setMsg(typeof d === 'string' ? d : 'Could not start crawl.');
    }
  };

  const running = jobs.some((j) => j.status === 'CRAWLING' || j.status === 'PENDING');

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-gray-900">Website Crawling</h1>
      <div className="card">
        <label className="label">Company or exhibitor-directory URL
          <input className="input mt-1" placeholder="https://example-company.com" value={url} onChange={(e) => setUrl(e.target.value)} />
        </label>
        <p className="mt-1 text-xs text-gray-500">Only public pages. Respects robots.txt, max 30 pages, ~1s between requests. Localhost/private addresses are blocked.</p>
        <button onClick={start} disabled={!url} className="btn-primary mt-3 text-sm"><Play className="mr-1 h-4 w-4" /> Start crawl</button>
        {msg && <p className="mt-2 text-sm text-gray-700">{msg}</p>}
      </div>
      <div className="card overflow-hidden p-0">
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-2">
          <h2 className="font-semibold">Crawl history</h2>
          <button onClick={load} className="btn-secondary text-xs"><RefreshCw className="mr-1 h-3 w-3" /> Refresh{running ? ' (auto)' : ''}</button>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr><th className="table-th">Job</th><th className="table-th">Pages</th><th className="table-th">Companies</th><th className="table-th">Contacts</th><th className="table-th">Status</th><th className="table-th"></th></tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {jobs.length === 0 && <tr><td colSpan={6} className="table-td text-center text-gray-400">No crawls yet.</td></tr>}
              {jobs.map((j) => (
                <tr key={j.id} className="hover:bg-gray-50">
                  <td className="table-td">#{j.id} <span className="block max-w-xs truncate text-xs text-gray-400">{j.url}</span></td>
                  <td className="table-td">{j.pages_visited}</td>
                  <td className="table-td">{j.companies_found}</td>
                  <td className="table-td">{j.contacts_found}</td>
                  <td className="table-td"><span className="badge bg-gray-100 text-gray-700">{j.status}</span></td>
                  <td className="table-td flex gap-1">
                    <button onClick={() => getCrawl(j.id).then(setDetail).catch(() => {})} className="btn-secondary text-xs">Details</button>
                    {(j.status === 'CRAWLING' || j.status === 'PENDING') && (
                      <button onClick={() => cancelCrawl(j.id).then(load)} className="btn-secondary text-xs"><X className="h-3 w-3" /></button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {detail && (
          <div className="border-t border-gray-200 p-4 text-sm">
            <p className="font-medium">Job #{detail.id as number} — {detail.status as string}</p>
            <p className="text-gray-500">Quota used today: {detail.quota_pages_used_today as number}/{detail.quota_pages_daily as number} pages</p>
            {((detail.errors as string[]) || []).length > 0 && (
              <ul className="mt-2 space-y-1 text-xs text-red-600">
                {((detail.errors as string[]) || []).slice(0, 8).map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
