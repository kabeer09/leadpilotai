import { useState } from 'react';
import { Search, Star, Mail, Globe, Phone, AlertTriangle, CheckCircle } from 'lucide-react';
import { researchCompany, generateDraft } from '../lib/api';

export default function Research() {
  const [companyId, setCompanyId] = useState('');
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState('');

  const research = async () => {
    if (!companyId) { setMsg('Enter a company ID.'); return; }
    setLoading(true);
    setMsg('');
    setResult(null);
    try {
      const r = await researchCompany(parseInt(companyId));
      setResult(r);
    } catch (e: unknown) {
      setMsg((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Research failed.');
    } finally {
      setLoading(false);
    }
  };

  const genDraft = async () => {
    if (!companyId) return;
    setLoading(true);
    try {
      const r = await generateDraft(parseInt(companyId));
      setResult((prev: any) => ({ ...prev, draft: r.draft }));
    } catch (e: unknown) {
      setMsg((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Draft generation failed.');
    } finally {
      setLoading(false);
    }
  };

  const qual = result?.qualification;
  const res = result?.research;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-gray-900">Buyer Research & Qualification</h1>
      <p className="text-sm text-gray-500">Research a company's website for contacts, qualify buyer fit, and generate outreach drafts.</p>

      <div className="card">
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="Company ID (from Companies page)"
            value={companyId}
            onChange={(e) => setCompanyId(e.target.value)}
          />
          <button onClick={research} disabled={loading} className="btn-primary text-sm">
            {loading ? 'Researching…' : 'Research'}
          </button>
        </div>
        {msg && <p className="mt-2 text-sm text-gray-700">{msg}</p>}
      </div>

      {result && (
        <>
          {/* Qualification Score */}
          {qual && (
            <div className="card">
              <div className="flex items-center gap-3">
                <div className={`flex h-12 w-12 items-center justify-center rounded-full text-lg font-bold text-white ${qual.score >= 70 ? 'bg-green-500' : qual.score >= 40 ? 'bg-yellow-500' : 'bg-gray-400'}`}>
                  {qual.score}
                </div>
                <div>
                  <h2 className="font-semibold">{result.company_name}</h2>
                  <p className="text-sm text-gray-500">
                    {qual.buyer_type} · {qual.product_fit} · {qual.country_fit}
                  </p>
                </div>
              </div>
              <div className="mt-3">
                <p className="text-sm font-medium text-gray-700">Recommended: {qual.recommended_next_action}</p>
                {qual.supporting_snippets?.length > 0 && (
                  <ul className="mt-2 space-y-1 text-xs text-gray-500">
                    {qual.supporting_snippets.map((s: string, i: number) => (
                      <li key={i}>• {s}</li>
                    ))}
                  </ul>
                )}
                {qual.missing_evidence?.length > 0 && (
                  <div className="mt-2">
                    <p className="text-xs font-medium text-yellow-700">Missing evidence:</p>
                    <ul className="text-xs text-yellow-600">
                      {qual.missing_evidence.map((m: string, i: number) => (
                        <li key={i}>• {m}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Research Results */}
          {res && (
            <div className="card">
              <h3 className="font-semibold">Contact Discovery</h3>
              <div className="mt-2 space-y-2">
                {res.emails?.length > 0 ? (
                  res.emails.map((e: any, i: number) => (
                    <div key={i} className="flex items-center gap-2 text-sm">
                      <Mail className="h-4 w-4 text-gray-400" />
                      <span>{e.value}</span>
                      <span className="badge bg-blue-100 text-blue-700 text-xs">{e.extraction_method}</span>
                      <span className="text-xs text-gray-400">conf: {(e.confidence * 100).toFixed(0)}%</span>
                    </div>
                  ))
                ) : (
                  <p className="text-sm text-gray-400">No emails found on company website.</p>
                )}
                {res.phones?.length > 0 && (
                  <div className="mt-2">
                    {res.phones.map((p: any, i: number) => (
                      <div key={i} className="flex items-center gap-2 text-sm">
                        <Phone className="h-4 w-4 text-gray-400" />
                        <span>{p.value}</span>
                        <span className="badge bg-blue-100 text-blue-700 text-xs">{p.extraction_method}</span>
                      </div>
                    ))}
                  </div>
                )}
                {res.whatsapp_links?.length > 0 && (
                  <div className="mt-2">
                    {res.whatsapp_links.map((w: any, i: number) => (
                      <div key={i} className="flex items-center gap-2 text-sm text-green-600">
                        <CheckCircle className="h-4 w-4" />
                        <span>WhatsApp: {w.value}</span>
                      </div>
                    ))}
                  </div>
                )}
                {res.description && (
                  <p className="mt-2 text-xs text-gray-500">{res.description}</p>
                )}
                {res.pages_checked?.length > 0 && (
                  <p className="mt-2 text-xs text-gray-400">Checked {res.pages_checked.length} pages</p>
                )}
                {res.errors?.length > 0 && (
                  <div className="mt-2">
                    {res.errors.map((e: string, i: number) => (
                      <p key={i} className="text-xs text-red-500">{e}</p>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Outreach Draft */}
          <div className="card">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">Outreach Draft</h3>
              {!result.draft && (
                <button onClick={genDraft} disabled={loading} className="btn-secondary text-sm">
                  Generate Draft
                </button>
              )}
            </div>
            {result.draft && (
              <div className="mt-3 space-y-2">
                <p className="text-sm font-medium">{result.draft.subject}</p>
                <pre className="whitespace-pre-wrap rounded bg-gray-50 p-3 text-xs text-gray-700">{result.draft.body}</pre>
                <p className="text-xs text-yellow-600">⚠️ Requires human approval before sending. Auto-send is disabled.</p>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
