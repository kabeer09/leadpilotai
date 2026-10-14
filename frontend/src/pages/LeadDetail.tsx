import { useState, useEffect } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { getLead, updateLead } from '../lib/api';

export default function LeadDetail() {
  const { id } = useParams();
  const [lead, setLead] = useState<Record<string, unknown> | null>(null);
  const [notes, setNotes] = useState('');
  const [msg, setMsg] = useState('');

  const load = async () => {
    if (!id) return;
    const d = await getLead(parseInt(id));
    setLead(d);
    setNotes((d.notes as string) || '');
  };
  useEffect(() => {
    load();
  }, [id]);

  if (!lead) return <p className="text-gray-500">Loading…</p>;
  const company = lead.company as { id: number; name: string } | null;
  const contacts = (lead.contacts as { value: string; kind: string; source_url: string | null; suppressed: boolean }[]) || [];
  const messages = (lead.messages as { id: number; to_email: string; subject: string | null; status: string; sent_at: string | null; is_test: boolean; is_followup: boolean; events: { type: string }[] }[]) || [];
  const followups = (lead.followups as { id: number; scheduled_at: string; status: string; skip_reason: string | null }[]) || [];
  const timeline = (lead.timeline as { at: string; text: string }[]) || [];

  return (
    <div className="space-y-4">
      <Link to="/leads" className="inline-flex items-center gap-1 text-sm text-primary-600"><ArrowLeft className="h-4 w-4" /> Leads</Link>
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-2xl font-bold text-gray-900">{company?.name || `Lead #${lead.id}`}</h1>
        <span className="badge bg-gray-100 text-gray-700">{lead.status as string}</span>
        <span className="text-sm text-gray-500">score {lead.score as number}</span>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card">
          <h2 className="font-semibold">Contacts</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {contacts.map((c, i) => (
              <li key={i}>{c.kind}: <span className="font-medium">{c.value}</span>
                {c.suppressed && <span className="badge ml-2 bg-red-100 text-red-700">suppressed</span>}
                {c.source_url && <a href={c.source_url} target="_blank" rel="noreferrer" className="block truncate text-xs text-primary-600">{c.source_url}</a>}
              </li>
            ))}
          </ul>
          <h2 className="mt-4 font-semibold">Notes</h2>
          <textarea className="input mt-2" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
          <button
            className="btn-secondary mt-2 text-sm"
            onClick={async () => {
              await updateLead(parseInt(id!), { notes });
              setMsg('Saved.');
              setTimeout(() => setMsg(''), 2000);
            }}
          >
            Save notes
          </button>
          {msg && <span className="ml-2 text-xs text-green-700">{msg}</span>}
        </div>
        <div className="card">
          <h2 className="font-semibold">Email activity ({messages.length})</h2>
          <ul className="mt-2 space-y-2 text-sm">
            {messages.map((m) => (
              <li key={m.id} className="rounded-lg border border-gray-100 p-2">
                <span className="font-medium">{m.subject || '(no subject)'}</span>{' '}
                <span className="badge bg-gray-100 text-gray-700">{m.status}</span>
                {m.is_test && <span className="badge ml-1 bg-blue-100 text-blue-700">test</span>}
                {m.is_followup && <span className="badge ml-1 bg-purple-100 text-purple-700">follow-up</span>}
                <p className="text-xs text-gray-500">to {m.to_email}{m.sent_at ? ` · ${new Date(m.sent_at).toLocaleString()}` : ''}</p>
                {m.events.length > 0 && <p className="text-xs text-gray-400">events: {m.events.map((e) => e.type).join(', ')}</p>}
              </li>
            ))}
            {messages.length === 0 && <p className="text-sm italic text-gray-400">No emails yet.</p>}
          </ul>
          <h2 className="mt-4 font-semibold">Follow-ups</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {followups.map((f) => (
              <li key={f.id}>{new Date(f.scheduled_at).toLocaleString()} — {f.status}{f.skip_reason ? ` (${f.skip_reason})` : ''}</li>
            ))}
            {followups.length === 0 && <p className="text-sm italic text-gray-400">None scheduled.</p>}
          </ul>
          {timeline.length > 0 && (
            <>
              <h2 className="mt-4 font-semibold">Notes timeline</h2>
              <ul className="mt-2 space-y-1 text-sm text-gray-600">
                {timeline.map((t, i) => <li key={i}>{new Date(t.at).toLocaleString()} — {t.text}</li>)}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
