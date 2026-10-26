import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Send } from 'lucide-react';
import { listCampaigns, createCampaign, campaignSend } from '../lib/api';

export default function Campaigns() {
  const [items, setItems] = useState<{ id: number; name: string; status: string }[]>([]);
  const [form, setForm] = useState({ name: '', subject: '', body: '' });
  const [batchLimit, setBatchLimit] = useState<number | null>(null);
  const [msg, setMsg] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [sending, setSending] = useState(false);
  const [allowResend, setAllowResend] = useState(false);
  const [recipientCount, setRecipientCount] = useState<number | null>(null);

  const load = async () => {
    try {
      setItems(await listCampaigns());
    } catch {}
  };
  useEffect(() => {
    load();
  }, []);

  const sendNow = async () => {
    setMsg('');
    setSending(true);
    try {
      let r = await createCampaign({ name: form.name, subject: form.subject, body: form.body, lead_ids: [], batch_limit: batchLimit, allow_resend: allowResend });
      // If 0 recipients due to already-contacted, auto-retry with resend enabled
      if (r.added === 0 && r.skipped_contacted > 0 && !allowResend) {
        r = await createCampaign({ name: form.name, subject: form.subject, body: form.body, lead_ids: [], batch_limit: batchLimit, allow_resend: true });
        setMsg('All leads were already contacted — resent to all of them.');
      }
      if (r.added === 0) {
        setMsg('No eligible recipients (all suppressed or no email).');
        setConfirming(false);
        setSending(false);
        return;
      }
      const s = await campaignSend(r.id, { confirm: true, recipient_count_ack: r.added });
      setMsg(`Sent: ${s.sent}, failed: ${s.failed}.`);
      setForm({ name: '', subject: '', body: '' });
      setConfirming(false);
      load();
    } catch (e: unknown) {
      setMsg((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Send failed.');
      setConfirming(false);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-gray-900">Email Campaigns</h1>
      <div className="card">
        <h2 className="flex items-center gap-2 font-semibold"><Plus className="h-4 w-4" /> New campaign</h2>
        <p className="mt-1 text-xs text-gray-500">Use {'{company}'}, {'{country}'}, {'{contact_email}'} placeholders.</p>
        <div className="mt-3 grid grid-cols-1 gap-3">
          <input className="input" placeholder="Campaign name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input className="input" placeholder="Subject — e.g. Partnership inquiry for {company}" value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
          <textarea className="input" rows={5} placeholder="Body…" value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} />
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-gray-600">Batch size:</span>
            {[5, 25, 50, 100, 500, 1000, 5000].map((n) => (
              <button
                key={n}
                onClick={() => setBatchLimit(batchLimit === n ? null : n)}
                className={`rounded-xl px-3 py-1.5 text-xs font-semibold ${batchLimit === n ? 'bg-primary-600 text-white' : 'border border-gray-200 bg-white text-gray-600 hover:bg-gray-50'}`}
              >
                {n}
              </button>
            ))}
            <span className="text-xs text-gray-500">
              {batchLimit ? `first ${batchLimit} leads` : 'all leads with email'}
            </span>
          </div>
          <label className="flex items-center gap-2 text-sm text-gray-600">
            <input type="checkbox" checked={allowResend} onChange={(e) => setAllowResend(e.target.checked)} className="rounded" />
            Resend to already contacted
          </label>
          <button onClick={() => setConfirming(true)} disabled={!form.name || !form.subject || !form.body} className="btn-primary w-fit text-sm">
            <Send className="mr-1 h-4 w-4" /> Send Email
          </button>
        </div>
        {msg && <p className="mt-2 text-sm text-gray-700">{msg}</p>}
        {confirming && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
            <div className="card w-full max-w-sm p-5">
              <h3 className="font-semibold">Send emails?</h3>
              <p className="mt-2 text-sm text-gray-600">This will send to all leads with email immediately. This cannot be undone.</p>
              <div className="mt-4 flex gap-2">
                <button onClick={sendNow} disabled={sending} className="btn-primary text-sm">
                  {sending ? 'Sending…' : 'Confirm send'}
                </button>
                <button onClick={() => setConfirming(false)} className="btn-secondary text-sm">Cancel</button>
              </div>
            </div>
          </div>
        )}
      </div>
      <div className="card overflow-hidden p-0">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50"><tr><th className="table-th">Name</th><th className="table-th">Status</th></tr></thead>
          <tbody className="divide-y divide-gray-200">
            {items.length === 0 && <tr><td colSpan={2} className="table-td text-center text-gray-400">No campaigns yet.</td></tr>}
            {items.map((c) => (
              <tr key={c.id} className="hover:bg-gray-50">
                <td className="table-td"><Link to={`/campaigns/${c.id}`} className="font-medium text-primary-600">{c.name}</Link></td>
                <td className="table-td"><span className="badge bg-gray-100 text-gray-700">{c.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
