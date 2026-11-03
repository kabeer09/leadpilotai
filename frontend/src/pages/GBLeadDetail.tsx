import { useState, useEffect } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowLeft, Globe, MapPin, ExternalLink, Star, Shield,
} from 'lucide-react';
import { gbGetLead, gbUpdateLead, gbEnrich } from '../lib/api';

type Evidence = {
  id: number; field_name: string; field_value: string; source_url: string;
  extraction_method: string | null; confidence: number | null;
  extracted_at: string;
};
type Detail = {
  id: number; company_name: string; business_category: string | null;
  business_description: string | null; business_type: string;
  business_type_evidence: string[]; types: string[];
  website: string | null; phone: string | null; email: string | null;
  country: string | null; state: string | null; city: string | null;
  address: string | null; google_maps_url: string | null;
  rating: number | null; review_count: number | null;
  relevance_score: number; relevance_label: string | null;
  match_reasons: string[]; enrichment_status: string; lead_status: string;
  place_id: string | null; campaign_id: number | null;
  enriched_at: string | null; created_at: string;
  evidence: Evidence[];
};

const relBadge = (label: string | null) => {
  switch (label) {
    case 'Highly Relevant': return 'bg-lime-100 text-lime-800';
    case 'Relevant': return 'bg-green-100 text-green-700';
    case 'Potential Match': return 'bg-amber-100 text-amber-700';
    default: return 'bg-gray-100 text-gray-600';
  }
};

const methodLabel = (m: string | null) => {
  switch (m) {
    case 'mailto_link': return 'Link displayed on the page (mailto:)';
    case 'text_regex': return 'Publicly displayed on official website';
    case 'tel_link': return 'Phone link displayed on the page (tel:)';
    case 'tel_text': return 'Phone number written on the page';
    case 'wa_link': return 'WhatsApp link explicitly published';
    case 'place_details': return 'Google Place Details';
    case 'meta_description': return 'Page meta description';
    case 'social_anchor': return 'Social link on official website';
    case 'page_text': return 'Page text';
    default: return m || '—';
  }
};
const confLabel = (c: number | null) =>
  c == null ? '—' : c >= 0.8 ? 'High' : c >= 0.55 ? 'Medium' : 'Low';

const errText = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
  (e as Error)?.message || 'Request failed';

export default function GBLeadDetail() {
  const { id } = useParams();
  const [lead, setLead] = useState<Detail | null>(null);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);

  const load = () => {
    if (!id) return;
    gbGetLead(parseInt(id))
      .then(setLead)
      .catch((e) => setErr(e?.response?.data?.detail || 'Failed to load lead'));
  };
  useEffect(load, [id]);

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(''), 4000); };

  const toggleSave = async () => {
    if (!lead) return;
    try {
      const d = await gbUpdateLead(lead.id, {
        lead_status: lead.lead_status === 'SAVED' ? 'NEW' : 'SAVED',
      });
      setLead({ ...lead, lead_status: d.lead_status });
    } catch (e) {
      setErr(errText(e) || 'Save failed');
    }
  };

  const enrichNow = async () => {
    if (!lead) return;
    setBusy(true); setErr('');
    try {
      await gbEnrich({ lead_ids: [lead.id] });
      flash('Enrichment queued — refresh in a few seconds.');
      setTimeout(load, 3000);
    } catch (e) {
      setErr(errText(e) || 'Failed to queue enrichment');
    } finally { setBusy(false); }
  };

  if (err && !lead) {
    return (
      <div className="space-y-3">
        <Link to="/business-leads" className="inline-flex items-center gap-1 text-sm text-primary-600">
          <ArrowLeft className="h-4 w-4" /> Business Leads
        </Link>
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{err}</div>
      </div>
    );
  }
  if (!lead) return <p className="text-gray-500">Loading…</p>;

  const socials = lead.evidence.filter((e) => e.field_name.startsWith('social_'));
  const emails = lead.evidence.filter((e) => e.field_name === 'email');
  const phones = lead.evidence.filter((e) => e.field_name === 'phone');
  const whatsapps = lead.evidence.filter((e) => e.field_name === 'whatsapp');

  return (
    <div className="space-y-4">
      <Link to="/business-leads" className="inline-flex items-center gap-1 text-sm text-primary-600">
        <ArrowLeft className="h-4 w-4" /> Business Leads
      </Link>

      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-2xl font-bold text-gray-900">{lead.company_name}</h1>
        <span className={`badge ${relBadge(lead.relevance_label)}`}>
          {lead.relevance_score} · {lead.relevance_label}
        </span>
        <span className="badge bg-gray-100 text-gray-700">{lead.business_type}</span>
        <span className="badge bg-gray-100 text-gray-700">
          {lead.enrichment_status.replace('_', ' ').toLowerCase()}
        </span>
        <span className="badge bg-gray-100 text-gray-700">{lead.lead_status}</span>
        <span className="ml-auto flex flex-wrap items-center gap-2">
          <button className="btn-secondary text-sm" onClick={toggleSave}>
            {lead.lead_status === 'SAVED' ? '★ Saved' : '☆ Save lead'}
          </button>
          {lead.website && lead.enrichment_status !== 'ENRICHED' && (
            <button className="btn-secondary text-sm" onClick={enrichNow} disabled={busy}>
              <Globe className="mr-1 h-4 w-4" />
              {busy ? 'Queuing…' : 'Enrich website'}
            </button>
          )}
          {lead.website && (
            <a className="btn-secondary text-sm" href={lead.website} target="_blank" rel="noreferrer">
              <ExternalLink className="mr-1 h-4 w-4" /> Website
            </a>
          )}
          {lead.google_maps_url && (
            <a className="btn-secondary text-sm" href={lead.google_maps_url} target="_blank" rel="noreferrer">
              <MapPin className="mr-1 h-4 w-4" /> Google Maps
            </a>
          )}
        </span>
      </div>

      {err && <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">{err}</div>}
      {msg && <div className="rounded-xl border border-green-200 bg-green-50 px-4 py-2 text-sm text-green-700">{msg}</div>}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* overview */}
        <div className="card lg:col-span-2">
          <h2 className="font-semibold">Company overview</h2>
          <p className="mt-2 whitespace-pre-wrap text-sm text-gray-700">
            {lead.business_description || 'No description available from Google.'}
          </p>
          <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            <div><dt className="text-gray-500">Category</dt>
              <dd className="font-medium">{lead.business_category || '—'}</dd></div>
            <div><dt className="text-gray-500">Business type</dt>
              <dd className="font-medium">{lead.business_type}</dd></div>
            <div><dt className="text-gray-500">Location</dt>
              <dd className="font-medium">
                {[lead.city, lead.state, lead.country].filter(Boolean).join(', ') || '—'}
              </dd></div>
            <div><dt className="text-gray-500">Address</dt>
              <dd className="font-medium">{lead.address || '—'}</dd></div>
            <div><dt className="text-gray-500">Website</dt>
              <dd className="font-medium break-all">
                {lead.website
                  ? <a className="text-primary-600" href={lead.website} target="_blank" rel="noreferrer">
                      {lead.website}
                    </a>
                  : '—'}
              </dd></div>
            <div><dt className="text-gray-500">Published phone</dt>
              <dd className="font-medium">{lead.phone || '—'}</dd></div>
            <div><dt className="text-gray-500">Published email</dt>
              <dd className="font-medium break-all">
                {lead.email
                  ? <a className="text-primary-600" href={`mailto:${lead.email}`}>{lead.email}</a>
                  : '—'}
              </dd></div>
            <div><dt className="text-gray-500">Google rating</dt>
              <dd className="font-medium">
                {lead.rating != null ? (
                  <span className="inline-flex items-center gap-1">
                    <Star className="h-4 w-4 fill-amber-400 text-amber-400" />
                    {lead.rating.toFixed(1)} ({lead.review_count ?? 0} reviews)
                  </span>
                ) : '—'}
              </dd></div>
          </dl>
          {(socials.length > 0 || whatsapps.length > 0) && (
            <div className="mt-4 text-sm">
              <span className="text-gray-500">Social & messaging: </span>
              <span className="flex flex-wrap gap-3">
                {socials.map((s) => (
                  <a key={s.id} href={s.field_value} target="_blank" rel="noreferrer"
                    className="text-primary-600 hover:underline">
                    {s.field_name === 'social_linkedin' ? 'LinkedIn' : 'Instagram'}
                  </a>
                ))}
                {whatsapps.map((w) => (
                  <a key={w.id} href={`https://wa.me/${w.field_value.replace(/\D/g, '')}`}
                    target="_blank" rel="noreferrer" className="text-green-700 hover:underline">
                    WhatsApp {w.field_value}
                  </a>
                ))}
              </span>
            </div>
          )}
        </div>

        {/* why it matches */}
        <div className="card">
          <h2 className="font-semibold">Why this lead matches</h2>
          <ul className="mt-2 space-y-2 text-sm">
            {lead.match_reasons.map((r, i) => (
              <li key={i} className="flex gap-2">
                <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-lime-500" />
                <span className="text-gray-700">{r}</span>
              </li>
            ))}
            {lead.match_reasons.length === 0 && (
              <li className="italic text-gray-400">No match evidence yet.</li>
            )}
          </ul>
          <h3 className="mt-4 text-sm font-semibold text-gray-700">
            Business type evidence
          </h3>
          <ul className="mt-1 space-y-1 text-xs text-gray-600">
            {lead.business_type_evidence.map((r, i) => <li key={i}>• {r}</li>)}
            {lead.business_type_evidence.length === 0 && (
              <li className="italic text-gray-400">
                No on-page evidence — classified as Unknown.
              </li>
            )}
          </ul>
          <div className="mt-4 text-xs text-gray-400">
            {lead.place_id && <>Place ID: <code>{lead.place_id}</code><br /></>}
            {lead.campaign_id && <>Campaign: #{lead.campaign_id}<br /></>}
            {lead.enriched_at && <>Enriched: {new Date(lead.enriched_at).toLocaleString()}</>}
          </div>
        </div>
      </div>

      {/* evidence */}
      <div className="card">
        <h2 className="flex items-center gap-2 font-semibold">
          <Shield className="h-4 w-4" /> Source evidence ({lead.evidence.length})
        </h2>
        <p className="mt-1 text-xs text-gray-500">
          Every extracted value below was literally published on the source page —
          nothing is guessed or generated. “Confidence” describes how the value was
          found, not whether it has been verified.
        </p>
        <div className="mt-3 overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-left text-xs uppercase tracking-wide text-gray-500">
                <th className="table-th">Field</th>
                <th className="table-th">Value</th>
                <th className="table-th">Source</th>
                <th className="table-th">Extraction</th>
                <th className="table-th">Confidence</th>
              </tr>
            </thead>
            <tbody>
              {lead.evidence.map((e) => (
                <tr key={e.id} className="border-b border-gray-100">
                  <td className="table-td whitespace-nowrap font-medium capitalize">
                    {e.field_name.replace('social_', '')}
                  </td>
                  <td className="table-td max-w-[260px] break-all">{e.field_value}</td>
                  <td className="table-td max-w-[280px]">
                    <a href={e.source_url} target="_blank" rel="noreferrer"
                      className="block truncate text-xs text-primary-600 hover:underline">
                      {e.source_url}
                    </a>
                  </td>
                  <td className="table-td text-xs text-gray-600">
                    {methodLabel(e.extraction_method)}
                  </td>
                  <td className="table-td whitespace-nowrap text-xs">
                    <span className={`badge ${
                      (e.confidence ?? 0) >= 0.8 ? 'bg-green-100 text-green-700'
                        : (e.confidence ?? 0) >= 0.55 ? 'bg-amber-100 text-amber-700'
                        : 'bg-gray-100 text-gray-600'}`}>
                      {confLabel(e.confidence)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {lead.evidence.length === 0 && (
            <p className="py-4 text-center text-sm italic text-gray-400">
              {lead.enrichment_status === 'NO_WEBSITE'
                ? 'This business has no website — there is nothing to extract.'
                : 'No evidence yet — run website enrichment to collect published contacts.'}
            </p>
          )}
        </div>

        {(emails.length > 0 || phones.length > 0) && (
          <div className="mt-4 grid grid-cols-1 gap-4 text-sm sm:grid-cols-2">
            <div>
              <h3 className="text-gray-700">Published emails</h3>
              <ul className="mt-1 space-y-1">
                {emails.map((e) => (
                  <li key={e.id} className="break-all">
                    <a className="text-primary-600" href={`mailto:${e.field_value}`}>
                      {e.field_value}
                    </a>
                    <a href={e.source_url} target="_blank" rel="noreferrer"
                      className="ml-2 text-xs text-gray-400 hover:underline">
                      source ↗
                    </a>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="text-gray-700">Published phones</h3>
              <ul className="mt-1 space-y-1">
                {phones.map((p) => (
                  <li key={p.id}>
                    {p.field_value}
                    <a href={p.source_url} target="_blank" rel="noreferrer"
                      className="ml-2 text-xs text-gray-400 hover:underline">
                      source ↗
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
