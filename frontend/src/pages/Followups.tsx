import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { listFollowups, processFollowups } from '../lib/api';

export default function Followups() {
  const [items, setItems] = useState<{ id: number; lead_id: number; company: string | null; scheduled_at: string; status: string; skip_reason: string | null }[]>([]);
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      setItems(await listFollowups());
    } catch {}
  };
  useEffect(() => {
    load();
  }, []);

  const run = async () => {
    setBusy(true);
    setMsg('');
    try {
      const r = await processFollowups();
      setMsg(`Processed: ${r.sent} sent, ${r.skipped} skipped (replies/suppressions/unknown states are never sent).`);
      load();
    } catch {
      setMsg('Processing failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold text-gray-900">Follow-ups</h1>
        <button onClick={run} disabled={busy} className="btn-primary text-sm">{busy ? 'Processing…' : 'Process due now'}</button>
      </div>
      <p className="text-sm text-gray-500">Default: one automatic follow-up, 24h after the first message. Each due item re-checks replies, bounces, unsubscribes and suppressions immediately before sending — unknown states are skipped and flagged, never sent.</p>
      {msg && <p className="rounded-lg bg-blue-50 p-3 text-sm text-blue-700">{msg}</p>}
      <div className="card overflow-hidden p-0">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr><th className="table-th">Lead</th><th className="table-th">Scheduled</th><th className="table-th">Status</th><th className="table-th">Reason</th></tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {items.length === 0 && <tr><td colSpan={4} className="table-td text-center text-gray-400">No follow-ups scheduled. Enable them per campaign.</td></tr>}
            {items.map((f) => (
              <tr key={f.id} className="hover:bg-gray-50">
                <td className="table-td"><Link to={`/leads/${f.lead_id}`} className="text-primary-600">{f.company || `#${f.lead_id}`}</Link></td>
                <td className="table-td text-xs">{new Date(f.scheduled_at).toLocaleString()}</td>
                <td className="table-td"><span className="badge bg-gray-100 text-gray-700">{f.status}</span></td>
                <td className="table-td text-xs">{f.skip_reason || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
