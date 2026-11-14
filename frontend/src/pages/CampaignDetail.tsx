import { useState, useEffect } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Send, MailCheck, AlertTriangle } from 'lucide-react';
import { campaignPreview, campaignTest, campaignSend } from '../lib/api';

interface PV {
  id: number; name: string; status: string; provider: string;
  provider_connected: boolean; recipient_count: number;
  pending_count: number; sendable_count: number; blocked_count: number;
  send_note: string;
  recipients: {
    lead_id: number; company: string; to_email: string; status: string;
    subject: string; body: string; error: string | null;
    lead_score: number | null; band: string | null;
    previously_contacted: boolean; suppressed: boolean;
  }[];
  daily_quota: { used: number; limit: number };
}

export default function CampaignDetail() {
  const { id } = useParams();
  const [pv, setPv] = useState<PV | null>(null);
  const [testTo, setTestTo] = useState('');
  const [msg, setMsg] = useState('');
  const [confirmStep, setConfirmStep] = useState(false);
  const [sending, setSending] = useState(false);

  const load = async () => {
    if (id) setPv(await campaignPreview(parseInt(id)).catch(() => null));
  };
  useEffect(() => {
    load();
  }, [id]);

  const sendTest = async () => {
    setMsg('');
    try {
      const r = await campaignTest(parseInt(id!), testTo ? { to_email: testTo } : {});
      setMsg(`Test processed via ${r.provider}${r.dry_run ? ' (dry-run — nothing left the server)' : ''} to ${r.to}.`);
    } catch (e: unknown) {
      setMsg((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Test failed.');
    }
  };

  // Ack must equal the PENDING count (what a send would really touch) —
  // never the raw recipient list, which may include already-sent rows.
  const sendLive = async () => {
    setMsg('');
    setSending(true);
    try {
      const r = await campaignSend(parseInt(id!), {
        confirm: true,
        recipient_count_ack: pv!.pending_count,
      });
      const skippedParts = Object.entries(r.skip_reasons || {})
        .map(([k, v]) => `${v} ${k.replace(/_/g, ' ')}`).join(', ');
      setMsg(`Sent: ${r.sent}, failed: ${r.failed}, skipped: ${r.skipped}` +
        (skippedParts ? ` (${skippedParts})` : '') + '.');
      setConfirmStep(false);
      load();
    } catch (e: unknown) {
      setMsg((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Send failed.');
    } finally {
      setSending(false);
    }
  };

  if (!pv) return <p className="text-gray-500">Loading…</p>;
  return (
    <div className="space-y-4">
      <Link to="/campaigns" className="inline-flex items-center gap-1 text-sm text-primary-600"><ArrowLeft className="h-4 w-4" /> Campaigns</Link>
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-2xl font-bold text-gray-900">{pv.name}</h1>
        <span className="badge bg-gray-100 text-gray-700">{pv.status}</span>
        <span className="text-xs text-gray-500">provider: {pv.provider}{pv.provider_connected ? '' : ' (not configured)'}</span>
        <span className="text-xs text-gray-500">quota: {pv.daily_quota.used}/{pv.daily_quota.limit} today</span>
      </div>
      {!pv.provider_connected && (
        <p className="rounded-lg bg-yellow-50 p-3 text-sm text-yellow-800">Email provider not configured — connect SMTP/Resend in Settings (server env). Dry-run still works.</p>
      )}
      <div className="card">
        <h2 className="font-semibold">1. Send a test (to your own inbox)</h2>
        <div className="mt-2 flex flex-col gap-2 sm:flex-row">
          <input className="input flex-1" placeholder="Defaults to your account email" value={testTo} onChange={(e) => setTestTo(e.target.value)} />
          <button onClick={sendTest} className="btn-secondary text-sm"><MailCheck className="mr-1 h-4 w-4" /> Send test</button>
        </div>
      </div>
      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-semibold">2. Preview recipients ({pv.recipient_count})</h2>
          <div className="flex gap-2 text-xs font-semibold">
            <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-emerald-700">{pv.sendable_count} sendable</span>
            {pv.blocked_count > 0 && (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-700">{pv.blocked_count} blocked</span>
            )}
            <span className="rounded-full bg-gray-100 px-2 py-0.5 text-gray-600">{pv.pending_count} pending</span>
          </div>
        </div>
        <p className="mt-1 text-[11px] text-gray-400">{pv.send_note}</p>
        <div className="mt-2 max-h-72 space-y-2 overflow-y-auto">
          {pv.recipients.map((r) => (
            <div key={r.lead_id} className="rounded-lg border border-gray-100 p-2 text-sm">
              <p>
                <Link to={`/leads/${r.lead_id}`} className="font-medium text-primary-600">{r.company}</Link>{' '}
                <span className="text-gray-500">&lt;{r.to_email}&gt;</span>{' '}
                <span className="badge ml-1 bg-gray-100 text-gray-700">{r.status}</span>
                {r.band && (
                  <span className="badge ml-1 bg-gray-100 text-gray-600">{r.band} · {r.lead_score}</span>
                )}
                {(r.suppressed || r.previously_contacted) && (
                  <span className="badge ml-1 bg-amber-100 text-amber-700">
                    {r.suppressed ? 'suppressed' : 'already contacted'}
                  </span>
                )}
              </p>
              {r.error && <p className="mt-1 text-xs text-amber-700">{r.error}</p>}
              <p className="mt-1 font-medium">{r.subject}</p>
              <p className="whitespace-pre-wrap text-xs text-gray-600">{r.body.slice(0, 400)}{r.body.length > 400 ? '…' : ''}</p>
            </div>
          ))}
          {pv.recipients.length === 0 && (
            <div className="space-y-2">
              <p className="text-sm italic text-gray-400">No recipients in this campaign.</p>
              <p className="text-sm text-gray-600">Create a new campaign on the <a href="/campaigns" className="text-primary-600 underline">Campaigns page</a> — already-contacted and suppressed addresses stay excluded (this app never re-mails).</p>
            </div>
          )}
        </div>
      </div>
      {pv.status === 'DRAFT' && pv.pending_count > 0 && (
        <div className="card">
          <h2 className="font-semibold">3. Live send — double confirmation</h2>
          {!confirmStep ? (
            <button onClick={() => setConfirmStep(true)} className="btn-primary mt-2 text-sm"><Send className="mr-1 h-4 w-4" /> I reviewed all {pv.recipient_count} recipients — continue</button>
          ) : (
            <div className="mt-2">
              <p className="text-sm text-gray-700">
                This will send <strong>{pv.sendable_count}</strong> of <strong>{pv.pending_count}</strong> pending emails via <strong>{pv.provider}</strong>
                {pv.blocked_count > 0 && <> ({pv.blocked_count} blocked recipient{pv.blocked_count === 1 ? '' : 's'} will be skipped)</>}.
                {' '}<span className="font-medium text-rose-600">This cannot be undone.</span>
              </p>
              <div className="mt-2 flex gap-2">
                <button onClick={sendLive} disabled={sending} className="btn-primary text-sm">
                  {sending ? 'Sending…' : `Confirm send to ${pv.pending_count} recipients`}
                </button>
                <button onClick={() => setConfirmStep(false)} className="btn-secondary text-sm">Cancel</button>
              </div>
            </div>
          )}
        </div>
      )}
      {msg && (
        <p className="flex items-start gap-1.5 rounded-lg bg-blue-50 p-3 text-sm text-blue-700">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {msg}
        </p>
      )}
    </div>
  );
}
