import { useState, useEffect } from 'react';
import { Download, Upload, Link2, CheckCircle, AlertCircle, XCircle, Clock } from 'lucide-react';
import { listSources, importCSV, importManualUrl, importTemplate } from '../lib/api';

const STATUS_COLORS: Record<string, string> = {
  READY: 'bg-green-100 text-green-800',
  READY_WITH_LIMITATIONS: 'bg-yellow-100 text-yellow-800',
  MANUAL_IMPORT_REQUIRED: 'bg-blue-100 text-blue-800',
  AUTHORIZATION_REQUIRED: 'bg-orange-100 text-orange-800',
  DISABLED_BY_POLICY: 'bg-red-100 text-red-800',
  TEMPORARILY_UNAVAILABLE: 'bg-gray-100 text-gray-700',
  CONFIGURATION_ERROR: 'bg-red-100 text-red-800',
};

const STATUS_ICONS: Record<string, JSX.Element> = {
  READY: <CheckCircle className="h-4 w-4" />,
  READY_WITH_LIMITATIONS: <AlertCircle className="h-4 w-4" />,
  MANUAL_IMPORT_REQUIRED: <Upload className="h-4 w-4" />,
  AUTHORIZATION_REQUIRED: <AlertCircle className="h-4 w-4" />,
  DISABLED_BY_POLICY: <XCircle className="h-4 w-4" />,
  TEMPORARILY_UNAVAILABLE: <Clock className="h-4 w-4" />,
  CONFIGURATION_ERROR: <XCircle className="h-4 w-4" />,
};

export default function Sources() {
  const [sources, setSources] = useState<any[]>([]);
  const [msg, setMsg] = useState('');
  const [manualUrl, setManualUrl] = useState('');
  const [manualName, setManualName] = useState('');
  const [manualEmail, setManualEmail] = useState('');
  const [manualPhone, setManualPhone] = useState('');
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      setSources(await listSources());
    } catch {}
  };
  useEffect(() => { load(); }, []);

  const handleCSV = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setMsg('');
    try {
      const r = await importCSV(file);
      setMsg(`Imported ${r.imported} companies, ${r.skipped} skipped.`);
      load();
    } catch (err: unknown) {
      setMsg((err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Import failed.');
    } finally {
      setBusy(false);
      e.target.value = '';
    }
  };

  const handleManual = async () => {
    if (!manualUrl || !manualName) { setMsg('URL and name required.'); return; }
    setBusy(true);
    setMsg('');
    try {
      await importManualUrl({ url: manualUrl, name: manualName, email: manualEmail, phone: manualPhone });
      setMsg(`Added ${manualName}.`);
      setManualUrl(''); setManualName(''); setManualEmail(''); setManualPhone('');
      load();
    } catch (err: unknown) {
      setMsg((err as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Failed.');
    } finally {
      setBusy(false);
    }
  };

  const downloadTemplate = async () => {
    const t = await importTemplate();
    const cols = t.columns.join(',');
    const example = t.columns.map((c: string) => t.example[c] || '').join(',');
    const csv = cols + '\n' + example + '\n';
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'leadpilot_import_template.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-gray-900">Trade Fair Sources</h1>
      <p className="text-sm text-gray-500">Source capability matrix — 12 fair sources with their access methods and status.</p>

      {msg && <p className="rounded-lg bg-blue-50 p-3 text-sm text-blue-700">{msg}</p>}

      {/* Source capability matrix */}
      <div className="card overflow-hidden p-0">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="table-th">Source</th>
              <th className="table-th">Status</th>
              <th className="table-th">Access Method</th>
              <th className="table-th">Notes</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {sources.map((s) => (
              <tr key={s.id} className="hover:bg-gray-50">
                <td className="table-td">
                  <p className="font-medium">{s.name}</p>
                  <p className="text-xs text-gray-400">{s.base_url}</p>
                </td>
                <td className="table-td">
                  <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[s.status] || 'bg-gray-100 text-gray-700'}`}>
                    {STATUS_ICONS[s.status]} {s.status.replace(/_/g, ' ')}
                  </span>
                </td>
                <td className="table-td text-xs">{s.access_method.replace(/_/g, ' ')}</td>
                <td className="table-td text-xs text-gray-500">{s.notes}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* CSV Import */}
      <div className="card">
        <h2 className="flex items-center gap-2 font-semibold"><Upload className="h-4 w-4" /> CSV Import</h2>
        <p className="mt-1 text-xs text-gray-500">For sources that require manual collection. Download the template, fill it in, and upload.</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button onClick={downloadTemplate} className="btn-secondary text-sm">
            <Download className="mr-1 h-4 w-4" /> Download Template
          </button>
          <label className="btn-primary cursor-pointer text-sm">
            <Upload className="mr-1 h-4 w-4" /> Upload CSV
            <input type="file" accept=".csv" className="hidden" onChange={handleCSV} disabled={busy} />
          </label>
        </div>
      </div>

      {/* Manual URL entry */}
      <div className="card">
        <h2 className="flex items-center gap-2 font-semibold"><Link2 className="h-4 w-4" /> Manual Company Entry</h2>
        <p className="mt-1 text-xs text-gray-500">Add a single company from a manually collected profile URL.</p>
        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
          <input className="input" placeholder="Company name *" value={manualName} onChange={(e) => setManualName(e.target.value)} />
          <input className="input" placeholder="Profile URL *" value={manualUrl} onChange={(e) => setManualUrl(e.target.value)} />
          <input className="input" placeholder="Email" value={manualEmail} onChange={(e) => setManualEmail(e.target.value)} />
          <input className="input" placeholder="Phone" value={manualPhone} onChange={(e) => setManualPhone(e.target.value)} />
        </div>
        <button onClick={handleManual} disabled={busy} className="btn-primary mt-3 text-sm">Add Company</button>
      </div>
    </div>
  );
}
