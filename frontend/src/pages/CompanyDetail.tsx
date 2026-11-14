import { useState, useEffect } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Flame } from 'lucide-react';
import { getCompany } from '../lib/api';

interface Factor { key: string; label: string; points: number; max: number; reason: string }
interface Score {
  score: number; max: number; band: string; factors: Factor[];
  engine: string; ai_generated: boolean; note: string;
}
interface C {
  id: number; name: string; product_summary: string | null; country: string | null;
  city: string | null; website: string | null; category: string | null;
  profile_url: string | null; lifecycle: string; lead_status: string;
  hall: string | null; booth: string | null; address: string | null;
  products: { name: string; category: string }[];
  contacts: { id: number; kind: string; value: string; source_url: string | null; source_page: string | null; verification: string }[];
  leads: { id: number; status: string; score: number }[];
  email_history: { id: number; to_email: string; subject: string | null; status: string; sent_at: string | null }[];
  crawl_history: { id: number; status: string; pages_visited: number }[];
  lead_score: Score | null;
  has_research: boolean;
  lead_score_note: string | null;
}

const BAND_BADGE: Record<string, string> = {
  Hot: 'bg-rose-100 text-rose-700',
  Warm: 'bg-amber-100 text-amber-700',
  Nurture: 'bg-sky-100 text-sky-700',
  Low: 'bg-gray-100 text-gray-600',
};

function LeadScoreCard({ c }: { c: C }) {
  const s = c.lead_score;
  if (!s) return null;
  // The factors must sum EXACTLY to the displayed score (backend contract);
  // if they ever don't, show the computed sum rather than a wrong headline.
  const sum = s.factors.reduce((a, f) => a + f.points, 0);
  const consistent = Math.abs(sum - s.score) < 0.051;
  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-1.5 font-semibold"><Flame className="h-4 w-4 text-amber-500" /> Lead score</h2>
        <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${BAND_BADGE[s.band] || 'bg-gray-100 text-gray-600'}`}>
          {s.band} · {consistent ? s.score : sum}/{s.max}
        </span>
      </div>
      <ul className="mt-3 space-y-2 text-sm">
        {s.factors.map((f) => (
          <li key={f.key} className="rounded-lg border border-gray-100 p-2">
            <div className="flex items-center justify-between">
              <span className="font-medium text-gray-700">{f.label}</span>
              <span className="text-xs font-bold text-gray-900">{f.points}/{f.max}</span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-gray-100">
              <div className="h-full rounded-full bg-primary-500" style={{ width: `${Math.round((f.points / f.max) * 100)}%` }} />
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-gray-400">{f.reason}</p>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-gray-400">{c.lead_score_note}</p>
      <p className="mt-1 text-[10px] text-gray-300">engine: {s.engine} · ai_generated: {String(s.ai_generated)}</p>
    </div>
  );
}

export default function CompanyDetail() {
  const { id } = useParams();
  const [c, setC] = useState<C | null>(null);
  useEffect(() => {
    if (id) getCompany(parseInt(id)).then(setC).catch(() => {});
  }, [id]);
  if (!c) return <p className="text-gray-500">Loading…</p>;
  return (
    <div className="space-y-4">
      <Link to="/companies" className="inline-flex items-center gap-1 text-sm text-primary-600"><ArrowLeft className="h-4 w-4" /> Companies</Link>
      <h1 className="text-2xl font-bold text-gray-900">{c.name}</h1>
      {!c.has_research && (
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800">
          No saved research for this company yet — the score below reflects only stored contacts and profile data, not buying intent.
        </p>
      )}
      <LeadScoreCard c={c} />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card">
          <h2 className="font-semibold">Profile</h2>
          <dl className="mt-2 space-y-1 text-sm">
            <div><dt className="inline text-gray-500">Website: </dt><dd className="inline">{c.website ? <a href={c.website.startsWith('http') ? c.website : `https://${c.website}`} target="_blank" rel="noreferrer" className="text-primary-600">{c.website}</a> : '—'}</dd></div>
            <div><dt className="inline text-gray-500">Location: </dt><dd className="inline">{[c.city, c.country].filter(Boolean).join(', ') || '—'}</dd></div>
            <div><dt className="inline text-gray-500">Category: </dt><dd className="inline">{c.category || '—'}</dd></div>
            {(c.hall || c.booth) && <div><dt className="inline text-gray-500">Stand: </dt><dd className="inline">{[c.hall, c.booth].filter(Boolean).join(' · ')}</dd></div>}
            {c.address && <div><dt className="inline text-gray-500">Address: </dt><dd className="inline">{c.address}</dd></div>}
            <div><dt className="inline text-gray-500">Lifecycle: </dt><dd className="inline">{c.lifecycle}</dd></div>
          </dl>
          {c.product_summary && <p className="mt-3 text-sm text-gray-700">{c.product_summary}</p>}
          {c.products && c.products.length > 0 && (
            <div className="mt-3">
              <p className="text-sm font-medium text-gray-700">Products ({c.products.length})</p>
              <ul className="mt-1 max-h-40 space-y-1 overflow-y-auto text-sm text-gray-600">
                {c.products.slice(0, 20).map((p, i) => <li key={i}>• {p.name}</li>)}
              </ul>
            </div>
          )}
        </div>
        <div className="card">
          <h2 className="font-semibold">Contacts ({c.contacts.length})</h2>
          <ul className="mt-2 space-y-2 text-sm">
            {c.contacts.map((x) => (
              <li key={x.id} className="rounded-lg border border-gray-100 p-2">
                <span className="badge bg-gray-100 text-gray-700">{x.kind}</span>{' '}
                <span className="font-medium">{x.value}</span>
                <span className="ml-2 text-xs text-gray-400">{x.verification}{x.source_page ? ` · ${x.source_page}` : ''}</span>
                {x.source_url && <a href={x.source_url} target="_blank" rel="noreferrer" className="block truncate text-xs text-primary-600">{x.source_url}</a>}
              </li>
            ))}
            {c.contacts.length === 0 && <p className="text-sm italic text-gray-400">No public contacts found.</p>}
          </ul>
        </div>
        <div className="card">
          <h2 className="font-semibold">Leads ({c.leads.length})</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {c.leads.map((l) => (
              <li key={l.id}><Link to={`/leads/${l.id}`} className="text-primary-600">Lead #{l.id}</Link> — {l.status} (score {l.score})</li>
            ))}
          </ul>
          <h2 className="mt-4 font-semibold">Email history</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {c.email_history.map((e) => (
              <li key={e.id}>{e.to_email} — {e.status}{e.sent_at ? ` · ${new Date(e.sent_at).toLocaleString()}` : ''}</li>
            ))}
            {c.email_history.length === 0 && <p className="text-sm italic text-gray-400">No emails yet.</p>}
          </ul>
        </div>
        <div className="card">
          <h2 className="font-semibold">Crawl history</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {c.crawl_history.map((j) => (
              <li key={j.id}>Job #{j.id} — {j.status} ({j.pages_visited} pages)</li>
            ))}
            {c.crawl_history.length === 0 && <p className="text-sm italic text-gray-400">No crawls recorded.</p>}
          </ul>
        </div>
      </div>
    </div>
  );
}
