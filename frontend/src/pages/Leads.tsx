import { useState, useEffect, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Search, Send } from 'lucide-react';
import { listLeads, createCampaign } from '../lib/api';

export default function Leads() {
  const navigate = useNavigate();
  const [items, setItems] = useState<{ id: number; status: string; score: number; company: string | null; email: string | null; country: string | null; suppressed: boolean; next_followup_at: string | null }[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [due, setDue] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [showCompose, setShowCompose] = useState(false);
  const [subject, setSubject] = useState('Halloween Sale is LIVE for {company} — up to 20% off metal decor');
  const [body, setBody] = useState('');
  const [msg, setMsg] = useState('');

  const toggle = (id: number) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const togglePage = () => {
    const ids = items.map((l) => l.id);
    setSelected((s) => (ids.every((i) => s.includes(i)) ? s.filter((i) => !ids.includes(i)) : [...new Set([...s, ...ids])]));
  };

  const load = useCallback(async () => {
    const d = await listLeads({ page, page_size: 25, ...(search ? { search } : {}), ...(status ? { status } : {}), ...(due ? { followup_due: true } : {}) });
    setItems(d.items);
    setTotal(d.total);
  }, [page, search, status, due]);

  useEffect(() => {
    const t = setTimeout(load, 300);
    return () => clearTimeout(t);
  }, [load]);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-gray-900">Leads ({total.toLocaleString()})</h1>
      <div className="flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input className="input pl-10" placeholder="Search company…" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1) }} />
        </div>
        <select className="input sm:w-44" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1) }}>
          <option value="">All statuses</option>
          {['NEW', 'CONTACTED', 'REPLIED', 'QUALIFIED', 'ARCHIVED'].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <label className="flex items-center gap-2 text-sm text-gray-600">
          <input type="checkbox" checked={due} onChange={(e) => { setDue(e.target.checked); setPage(1) }} /> Follow-up due
        </label>
      </div>
      <div className="card overflow-hidden p-0">
        <div className="overflow-x-auto">
          {selected.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 border-b border-gray-200 bg-primary-50 px-4 py-2 text-sm">
              <span className="font-medium">{selected.length} selected</span>
              <button onClick={() => setShowCompose(true)} className="btn-primary text-xs"><Send className="mr-1 h-3 w-3" /> Compose email</button>
              <button onClick={() => setSelected([])} className="btn-secondary text-xs">Clear</button>
            </div>
          )}
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr><th className="table-th"><input type="checkbox" checked={items.length > 0 && items.every((l) => selected.includes(l.id))} onChange={togglePage} /></th><th className="table-th">Company</th><th className="table-th">Email</th><th className="table-th">Score</th><th className="table-th">Status</th></tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {items.length === 0 ? (
                <tr><td colSpan={5} className="table-td text-center text-gray-400">{total === 0 ? 'No leads yet — crawl a website first.' : 'No matches.'}</td></tr>
              ) : items.map((l) => (
                <tr key={l.id} className="hover:bg-gray-50">
                  <td className="table-td"><input type="checkbox" checked={selected.includes(l.id)} onChange={() => toggle(l.id)} /></td>
                  <td className="table-td"><Link to={`/leads/${l.id}`} className="font-medium text-primary-600">{l.company || `#${l.id}`}</Link></td>
                  <td className="table-td text-xs">{l.email || '—'}{l.suppressed && <span className="badge ml-2 bg-red-100 text-red-700">suppressed</span>}</td>
                  <td className="table-td">{l.score}</td>
                  <td className="table-td"><span className="badge bg-gray-100 text-gray-700">{l.status}</span></td>
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
      {showCompose && (
        <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4">
          <div className="card w-full max-w-lg">
            <h2 className="font-semibold">Email {selected.length} selected leads</h2>
            <p className="mt-1 text-xs text-gray-500">Use {'{company}'}, {'{country}'}, {'{contact_email}'}. Creates a draft → preview + test + confirm on the next screen. Only these leads queue.</p>
            <input className="input mt-3" placeholder="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
            <textarea className="input mt-2" rows={7} placeholder="Body…" value={body} onChange={(e) => setBody(e.target.value)} />
            {msg && <p className="mt-2 text-sm text-red-600">{msg}</p>}
            <div className="mt-3 flex gap-2">
              <button
                className="btn-primary text-sm"
                disabled={!subject || !body}
                onClick={async () => {
                  try {
                    const r = await createCampaign({ name: `Quick send ${new Date().toLocaleString()}`, subject, body, lead_ids: selected });
                    setShowCompose(false);
                    setSelected([]);
                    navigate(`/campaigns/${r.id}`);
                  } catch (e: unknown) {
                    setMsg((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Create failed.');
                  }
                }}
              >
                Continue to preview & send
              </button>
              <button onClick={() => setShowCompose(false)} className="btn-secondary text-sm">Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
