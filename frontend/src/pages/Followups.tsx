import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { listFollowups, processFollowups } from '../lib/api';

export default function Followups() {
  const [items, setItems] = useState<{ id: number; lead_id: number; company: string | null; scheduled_at: string; status: string; skip_reason: string | null; to_email: string | null; subject: string | null; body: string | null }[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState('SCHEDULED');

  const load = async () => {
    try {
      const all = await listFollowups();
      setItems(all);
    } catch {}
  };
  useEffect(() => {
    load();
  }, []);

  const filtered = filter === 'ALL' ? items : items.filter((f) => f.status === filter);

  const allSelected = filtered.length > 0 && selected.size === filtered.length;
  const toggleAll = () => {
    setSelected(allSelected ? new Set() : new Set(filtered.map((f) => f.id)));
  };
  const toggle = (id: number) => {
    const s = new Set(selected);
    if (s.has(id)) s.delete(id);
    else s.add(id);
    setSelected(s);
  };

  const run = async () => {
    setBusy(true);
    setMsg('');
    try {
      const r = await processFollowups();
      setMsg(`Processed: ${r.sent} sent, ${r.skipped} skipped (replies/suppressions/unknown states are never sent).`);
      setSelected(new Set());
      load();
    } catch {
      setMsg('Processing failed.');
    } finally {
      setBusy(false);
    }
  };

  const scheduledCount = items.filter((f) => f.status === 'SCHEDULED').length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold text-gray-900">Follow-ups</h1>
        <div className="flex gap-2">
          <button onClick={run} disabled={busy || scheduledCount === 0} className="btn-primary text-sm">{busy ? 'Processing…' : `Process due now (${scheduledCount})`}</button>
        </div>
      </div>
      <p className="text-sm text-gray-500">Default: one automatic follow-up, 24h after the first message. Each due item re-checks replies, bounces, unsubscribes and suppressions immediately before sending — unknown states are skipped and flagged, never sent.</p>
      {msg && <p className="rounded-lg bg-blue-50 p-3 text-sm text-blue-700">{msg}</p>}
      <div className="flex gap-2">
        {['SCHEDULED', 'SENT', 'SKIPPED', 'ALL'].map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`rounded-xl px-3 py-1.5 text-xs font-semibold ${filter === s ? 'bg-primary-600 text-white' : 'border border-gray-200 bg-white text-gray-600 hover:bg-gray-50'}`}
          >
            {s} ({items.filter((f) => s === 'ALL' || f.status === s).length})
          </button>
        ))}
      </div>
      <div className="card overflow-hidden p-0">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="table-th w-8"><input type="checkbox" checked={allSelected} onChange={toggleAll} /></th>
              <th className="table-th">Lead</th>
              <th className="table-th">Email</th>
              <th className="table-th">Scheduled</th>
              <th className="table-th">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {filtered.length === 0 && <tr><td colSpan={5} className="table-td text-center text-gray-400">No follow-ups with status "{filter}".</td></tr>}
            {filtered.map((f) => (
              <tr key={f.id} className="hover:bg-gray-50 align-top">
                <td className="table-td"><input type="checkbox" checked={selected.has(f.id)} onChange={() => toggle(f.id)} disabled={f.status !== 'SCHEDULED'} /></td>
                <td className="table-td"><Link to={`/leads/${f.lead_id}`} className="text-primary-600">{f.company || `#${f.lead_id}`}</Link></td>
                <td className="table-td text-xs">
                  {f.to_email ? (
                    <>
                      <p className="text-gray-700">{f.to_email}</p>
                      <p className="font-medium text-gray-800">{f.subject}</p>
                      <p className="mt-1 whitespace-pre-wrap text-gray-500">{f.body?.slice(0, 200)}{f.body && f.body.length > 200 ? '…' : ''}</p>
                    </>
                  ) : (
                    <span className="text-gray-400">—</span>
                  )}
                </td>
                <td className="table-td text-xs">{new Date(f.scheduled_at).toLocaleString()}</td>
                <td className="table-td"><span className="badge bg-gray-100 text-gray-700">{f.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
