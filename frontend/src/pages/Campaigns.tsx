import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Plus } from 'lucide-react';
import { listCampaigns, createCampaign } from '../lib/api';

export default function Campaigns() {
  const [items, setItems] = useState<{ id: number; name: string; status: string }[]>([]);
  const [form, setForm] = useState({ name: '', subject: '', body: '', leadIds: '' });
  const [msg, setMsg] = useState('');

  const load = async () => {
    try {
      setItems(await listCampaigns());
    } catch {}
  };
  useEffect(() => {
    load();
  }, []);

  const create = async () => {
    setMsg('');
    try {
      // Offer all leads as selectable IDs; keep simple: comma-separated lead IDs
      const leadIds = form.leadIds.split(',').map((s) => parseInt(s.trim())).filter((n) => !isNaN(n));
      const r = await createCampaign({ name: form.name, subject: form.subject, body: form.body, lead_ids: leadIds });
      setMsg(`Draft created: ${r.added} recipients added (${r.skipped_suppressed} suppressed, ${r.skipped_contacted} already contacted, ${r.skipped_noemail} no email).`);
      setForm({ name: '', subject: '', body: '', leadIds: '' });
      load();
    } catch (e: unknown) {
      setMsg((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Create failed.');
    }
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-gray-900">Email Campaigns</h1>
      <div className="card">
        <h2 className="flex items-center gap-2 font-semibold"><Plus className="h-4 w-4" /> New campaign (draft)</h2>
        <p className="mt-1 text-xs text-gray-500">Use {'{company}'}, {'{country}'}, {'{contact_email}'} placeholders. Campaigns start as DRAFT — sending needs preview + double confirmation.</p>
        <div className="mt-3 grid grid-cols-1 gap-3">
          <input className="input" placeholder="Campaign name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input className="input" placeholder="Subject — e.g. Partnership inquiry for {company}" value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
          <textarea className="input" rows={5} placeholder="Body…" value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} />
          <input className="input" placeholder="Lead IDs, comma-separated (see Leads page)" value={form.leadIds} onChange={(e) => setForm({ ...form, leadIds: e.target.value })} />
          <button onClick={create} disabled={!form.name || !form.subject || !form.body} className="btn-primary w-fit text-sm">Create draft</button>
        </div>
        {msg && <p className="mt-2 text-sm text-gray-700">{msg}</p>}
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
