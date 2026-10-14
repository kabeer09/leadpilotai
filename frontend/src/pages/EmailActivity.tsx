import { useState, useEffect } from 'react';
import { emailEvents, inboundEvent } from '../lib/api';

export default function EmailActivity() {
  const [items, setItems] = useState<{ id: number; to_email: string; subject: string | null; status: string; provider: string; is_test: boolean; is_followup: boolean; error: string | null; sent_at: string | null; created_at: string }[]>([]);
  const [status, setStatus] = useState('');
  const [inb, setInb] = useState({ event_type: 'reply', email: '', body: '' });
  const [msg, setMsg] = useState('');

  const load = async () => {
    try {
      setItems(await emailEvents(status ? { status } : undefined));
    } catch {}
  };
  useEffect(() => {
    load();
  }, [status]);

  const submitInbound = async () => {
    setMsg('');
    try {
      const r = await inboundEvent(inb);
      setMsg(`${r.message}${r.suppressed ? ' — address suppressed.' : ''}`);
      setInb({ event_type: 'reply', email: '', body: '' });
      load();
    } catch {
      setMsg('Failed to record event.');
    }
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-gray-900">Email Activity</h1>
      <div className="card">
        <h2 className="font-semibold">Record provider event (reply / bounce / unsubscribe / delivered)</h2>
        <p className="mt-1 text-xs text-gray-500">Paste a provider webhook payload or log an event manually. Replies and unsubscribes stop follow-ups immediately.</p>
        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
          <select className="input" value={inb.event_type} onChange={(e) => setInb({ ...inb, event_type: e.target.value })}>
            {['reply', 'bounce', 'unsubscribe', 'delivered'].map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <input className="input" placeholder="recipient email" value={inb.email} onChange={(e) => setInb({ ...inb, email: e.target.value })} />
          <input className="input" placeholder="body / note (optional)" value={inb.body} onChange={(e) => setInb({ ...inb, body: e.target.value })} />
        </div>
        <button onClick={submitInbound} disabled={!inb.email} className="btn-secondary mt-2 text-sm">Record event</button>
        {msg && <p className="mt-2 text-sm text-gray-700">{msg}</p>}
      </div>
      <div className="flex gap-2">
        <select className="input sm:w-48" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          {['queued', 'accepted', 'delivered', 'bounced', 'failed', 'replied'].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>
      <div className="card overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr><th className="table-th">To</th><th className="table-th">Subject</th><th className="table-th">Status</th><th className="table-th">Flags</th><th className="table-th">Sent</th></tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {items.length === 0 && <tr><td colSpan={5} className="table-td text-center text-gray-400">No email events yet. Statuses: queued → accepted (provider took it) → delivered (only on provider proof). Accepted is never shown as delivered.</td></tr>}
              {items.map((m) => (
                <tr key={m.id} className="hover:bg-gray-50">
                  <td className="table-td text-xs">{m.to_email}</td>
                  <td className="table-td text-xs">{m.subject || '—'}</td>
                  <td className="table-td"><span className="badge bg-gray-100 text-gray-700">{m.status}</span>{m.error && <p className="text-xs text-red-600">{m.error.slice(0, 120)}</p>}</td>
                  <td className="table-td text-xs">{[m.is_test && 'test', m.is_followup && 'follow-up'].filter(Boolean).join(', ') || '—'}</td>
                  <td className="table-td text-xs">{m.sent_at ? new Date(m.sent_at).toLocaleString() : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
