import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Send, AlertTriangle } from 'lucide-react';
import { listCampaigns, createCampaign, campaignPreview, campaignSend } from '../lib/api';

interface Created {
  id: number;
  added: number;
  skipped_suppressed: number;
  skipped_contacted: number;
  skipped_noemail: number;
  skipped_unresearched: number;
  skipped_low_fit: number;
}
interface Preview {
  id: number;
  pending_count: number;
  sendable_count: number;
  blocked_count: number;
  recipient_count: number;
  send_note: string;
}
interface SendResult {
  sent: number;
  failed: number;
  skipped: number;
  skip_reasons: Record<string, number>;
  errors: string[];
}

// Honest label for the "0 added" breakdown — never a generic failure.
function skipBreakdown(r: Created): string {
  const parts: string[] = [];
  if (r.skipped_contacted) parts.push(`${r.skipped_contacted} already contacted (never re-mailed)`);
  if (r.skipped_suppressed) parts.push(`${r.skipped_suppressed} suppressed`);
  if (r.skipped_noemail) parts.push(`${r.skipped_noemail} no email`);
  if (r.skipped_unresearched) parts.push(`${r.skipped_unresearched} no saved research`);
  if (r.skipped_low_fit) parts.push(`${r.skipped_low_fit} below buyer-fit threshold`);
  return parts.length ? `No recipients added: ${parts.join(', ')}.` : 'No eligible recipients (all suppressed or no email).';
}

export default function Campaigns() {
  const [items, setItems] = useState<{ id: number; name: string; status: string }[]>([]);
  const [form, setForm] = useState({ name: '', subject: '', body: '' });
  const [batchLimit, setBatchLimit] = useState<number | null>(null);
  const [requireResearch, setRequireResearch] = useState(false);
  const [minBuyerFit, setMinBuyerFit] = useState<number | null>(null);
  const [msg, setMsg] = useState('');
  const [msgKind, setMsgKind] = useState<'info' | 'warn' | 'ok'>('info');
  const [created, setCreated] = useState<Created | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [sending, setSending] = useState(false);

  const load = async () => {
    try {
      setItems(await listCampaigns());
    } catch {}
  };
  useEffect(() => {
    load();
  }, []);

  // Step 1: build the recipient list (NO send yet — nothing irreversible).
  const build = async () => {
    setMsg('');
    setSending(true);
    try {
      const r: Created = await createCampaign({
        name: form.name, subject: form.subject, body: form.body,
        lead_ids: [], batch_limit: batchLimit,
        require_research: requireResearch,
        min_buyer_fit: minBuyerFit,
        // NEVER auto-resend: already-contacted leads stay excluded, always.
        allow_resend: false,
      });
      setCreated(r);
      if (r.added === 0) {
        setMsg(skipBreakdown(r));
        setMsgKind('warn');
        setSending(false);
        return;
      }
      const pv: Preview = await campaignPreview(r.id);
      setPreview(pv);
      setConfirming(true);
    } catch (e: unknown) {
      setMsg((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Could not build the campaign.');
      setMsgKind('warn');
      setCreated(null);
      setPreview(null);
    } finally {
      setSending(false);
    }
  };

  // Step 2: send with the EXACT pending count as acknowledgement.
  const sendNow = async () => {
    if (!created || !preview) return;
    setMsg('');
    setSending(true);
    try {
      const s: SendResult = await campaignSend(created.id, {
        confirm: true,
        recipient_count_ack: preview.pending_count,
      });
      const skippedParts = Object.entries(s.skip_reasons || {})
        .map(([k, v]) => `${v} ${k.replace(/_/g, ' ')}`).join(', ');
      setMsg(
        `Sent: ${s.sent}, failed: ${s.failed}, skipped: ${s.skipped}` +
        (skippedParts ? ` (${skippedParts})` : '') + '.'
      );
      setMsgKind(s.failed > 0 || s.sent === 0 ? 'warn' : 'ok');
      setForm({ name: '', subject: '', body: '' });
      setCreated(null);
      setPreview(null);
      setConfirming(false);
      load();
    } catch (e: unknown) {
      setMsg((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Send failed.');
      setMsgKind('warn');
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
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-sm text-gray-600">
              <input type="checkbox" checked={requireResearch} onChange={(e) => setRequireResearch(e.target.checked)} className="rounded" />
              Only companies with saved research
            </label>
            <label className="flex items-center gap-2 text-sm text-gray-600">
              <span className="font-medium">Min buyer fit:</span>
              <select
                className="input w-32 py-1 text-xs"
                value={minBuyerFit ?? ''}
                onChange={(e) => setMinBuyerFit(e.target.value === '' ? null : parseInt(e.target.value, 10))}
              >
                <option value="">any</option>
                {[30, 50, 70, 85].map((n) => <option key={n} value={n}>{n}+</option>)}
              </select>
            </label>
          </div>
          <p className="text-[11px] text-gray-400">
            Already-contacted and suppressed addresses are always excluded — this app never re-mails.
          </p>
          <button onClick={build} disabled={!form.name || !form.subject || !form.body || sending} className="btn-primary w-fit text-sm">
            <Send className="mr-1 h-4 w-4" /> {sending ? 'Building…' : 'Review recipients'}
          </button>
        </div>
        {msg && (
          <p className={`mt-2 flex items-start gap-1.5 text-sm ${msgKind === 'ok' ? 'text-emerald-700' : msgKind === 'warn' ? 'text-amber-700' : 'text-gray-700'}`}>
            {msgKind === 'warn' && <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />}
            {msg}
          </p>
        )}
        {confirming && preview && created && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
            <div className="card w-full max-w-sm p-5">
              <h3 className="font-semibold">Send {preview.pending_count} email{preview.pending_count === 1 ? '' : 's'}?</h3>
              <ul className="mt-2 space-y-1 text-sm text-gray-600">
                <li><span className="font-bold text-gray-900">{preview.sendable_count}</span> will be mailed now</li>
                {preview.blocked_count > 0 && (
                  <li><span className="font-bold text-amber-700">{preview.blocked_count}</span> blocked (suppressed / already contacted — skipped at send time)</li>
                )}
                <li className="text-xs text-gray-400">{preview.send_note}</li>
              </ul>
              <p className="mt-2 text-sm font-medium text-rose-600">This cannot be undone.</p>
              <div className="mt-4 flex gap-2">
                <button onClick={sendNow} disabled={sending} className="btn-primary text-sm">
                  {sending ? 'Sending…' : `Confirm send (${preview.pending_count})`}
                </button>
                <button onClick={() => { setConfirming(false); setPreview(null); }} className="btn-secondary text-sm">Cancel</button>
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
