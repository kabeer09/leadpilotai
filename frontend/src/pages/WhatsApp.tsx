import { useState, useEffect } from 'react';
import { MessageCircle, ExternalLink, Search, CheckCircle, AlertCircle } from 'lucide-react';
import { whatsappLeads, whatsappSend } from '../lib/api';

interface Lead {
  lead_id: number;
  company: string;
  phone: string;
  whatsapp_link: string;
  status: string;
}

export default function WhatsApp() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [message, setMessage] = useState('');
  const [results, setResults] = useState<{ lead_id: number; company: string; phone: string; link: string }[]>([]);
  const [msg, setMsg] = useState('');
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');

  const load = async () => {
    try {
      const r = await whatsappLeads();
      setLeads(r.leads || []);
    } catch { setMsg('Failed to load leads.'); }
  };
  useEffect(() => { load(); }, []);

  const send = async () => {
    if (!message.trim()) { setMsg('Write a message first.'); return; }
    setLoading(true);
    setMsg('');
    try {
      const r = await whatsappSend({ message });
      setResults(r.links || []);
      setMsg(`Generated ${r.count} WhatsApp links. Click any to open WhatsApp.`);
    } catch (e: unknown) {
      setMsg((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Failed.');
    } finally {
      setLoading(false);
    }
  };

  const filtered = leads.filter((l) =>
    l.company.toLowerCase().includes(search.toLowerCase()) ||
    l.phone.includes(search)
  );

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-gray-900">WhatsApp Automation</h1>
      <div className="card">
        <h2 className="flex items-center gap-2 font-semibold"><MessageCircle className="h-4 w-4" /> Bulk WhatsApp Sender</h2>
        <p className="mt-1 text-xs text-gray-500">Use {'{company}'} placeholder. Generates click-to-chat links for all leads with phone numbers.</p>
        <div className="mt-3 space-y-3">
          <textarea
            className="input"
            rows={4}
            placeholder="Hi {company}, this is New Look Impex from Moradabad. We have a special Halloween sale on metal decor — up to 20% off. Interested?"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
          <button onClick={send} disabled={loading || !message.trim()} className="btn-primary text-sm">
            {loading ? 'Generating…' : `Generate WhatsApp Links (${leads.length} leads)`}
          </button>
        </div>
        {msg && <p className="mt-2 text-sm text-gray-700">{msg}</p>}
      </div>

      {results.length > 0 && (
        <div className="card overflow-hidden p-0">
          <div className="border-b border-gray-100 p-3">
            <h3 className="font-semibold">Send via WhatsApp ({results.length})</h3>
          </div>
          <div className="max-h-96 overflow-y-auto">
            {results.map((r) => (
              <div key={r.lead_id} className="flex items-center justify-between border-b border-gray-50 p-3 text-sm">
                <div>
                  <p className="font-medium">{r.company}</p>
                  <p className="text-xs text-gray-500">{r.phone}</p>
                </div>
                <a href={r.link} target="_blank" rel="noopener noreferrer" className="btn-secondary text-xs">
                  <ExternalLink className="mr-1 h-3 w-3" /> Open WhatsApp
                </a>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="card overflow-hidden p-0">
        <div className="flex items-center gap-2 border-b border-gray-100 p-3">
          <h3 className="font-semibold">All Leads with Phone ({leads.length})</h3>
          <div className="ml-auto relative">
            <Search className="absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-gray-400" />
            <input
              className="input pl-7 py-1 text-xs"
              placeholder="Search…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>
        <div className="max-h-96 overflow-y-auto">
          {filtered.map((l) => (
            <div key={l.lead_id} className="flex items-center justify-between border-b border-gray-50 p-3 text-sm">
              <div>
                <p className="font-medium">{l.company}</p>
                <p className="text-xs text-gray-500">{l.phone}</p>
              </div>
              <div className="flex items-center gap-2">
                {l.status === 'verified' ? (
                  <span className="flex items-center gap-1 text-xs text-green-600"><CheckCircle className="h-3 w-3" /> Verified</span>
                ) : (
                  <span className="flex items-center gap-1 text-xs text-yellow-600"><AlertCircle className="h-3 w-3" /> Likely</span>
                )}
                <a
                  href={`https://wa.me/${l.phone.replace(/\D/g, '')}?text=${encodeURIComponent(message || 'Hi ' + l.company)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-primary-600 hover:underline"
                >
                  <ExternalLink className="mr-1 h-3 w-3" /> Chat
                </a>
              </div>
            </div>
          ))}
          {filtered.length === 0 && <p className="p-4 text-center text-sm text-gray-400">No leads found.</p>}
        </div>
      </div>
    </div>
  );
}
