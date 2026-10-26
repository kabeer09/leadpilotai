import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { googleConsume } from '../lib/api';
import { useAuth } from '../auth';
import api from '../lib/api';

function saveTokens(t: { access_token: string; refresh_token: string }) {
  localStorage.setItem('lp_access', t.access_token);
  localStorage.setItem('lp_refresh', t.refresh_token);
}

export function GoogleButton({ label }: { label: string }) {
  const [error, setError] = useState('');
  const go = async () => {
    setError('');
    try {
      const { auth_url } = await (await import('../lib/api')).googleLoginUrl();
      window.location.href = auth_url;
    } catch (e: unknown) {
      const d = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      setError(d || 'Google login is not configured on this server yet.');
    }
  };
  return (
    <div>
      <button className="btn-secondary w-full" onClick={go}>
        <svg className="mr-2 h-4 w-4" viewBox="0 0 24 24">
          <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1z" />
          <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23z" />
          <path fill="#FBBC05" d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84z" />
          <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15A11 11 0 0 0 2.18 7.06l3.66 2.84C6.71 7.31 9.14 5.38 12 5.38z" />
        </svg>
        {label}
      </button>
      {error && <p className="mt-2 text-center text-xs text-red-600">{error}</p>}
      <p className="mt-1 text-center text-[11px] text-gray-400">One consent signs you in and connects Gmail sending.</p>
    </div>
  );
}

export function GoogleSetup({ onSaved }: { onSaved: () => void }) {
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [redirectUri, setRedirectUri] = useState('');
  const [msg, setMsg] = useState('');
  const [open, setOpen] = useState(false);

  useEffect(() => {
    api.get('/auth/google/client-status').then((r) => {
      if (!r.data.configured) setRedirectUri(r.data.redirect_uri || '');
    }).catch(() => {});
  }, []);

  const save = async () => {
    setMsg('');
    try {
      const r = await api.post('/auth/google/client', {
        client_id: clientId, client_secret: clientSecret || undefined,
      });
      setRedirectUri(r.data.redirect_uri || redirectUri);
      setMsg('Saved! Continue with Google now works.');
      setClientId('');
      setClientSecret('');
      onSaved();
    } catch (e: unknown) {
      const d = (e as { response?: { status?: number; data?: { detail?: string } } });
      setMsg(d.response?.data?.detail || (d.response?.status === 403
        ? 'Already configured — sign in as admin to replace it.'
        : 'Save failed.'));
    }
  };

  return (
    <div className="mt-3 rounded-xl border border-gray-200 bg-gray-50 p-3">
      <button onClick={() => setOpen(!open)} className="text-xs font-semibold text-primary-600">
        {open ? 'Hide setup' : 'No Google yet? Connect your own in 2 minutes'}
      </button>
      {open && (
        <div className="mt-2 space-y-2 text-xs text-gray-600">
          <ol className="list-decimal space-y-1 pl-4">
            <li>Open <a href="https://console.cloud.google.com" target="_blank" rel="noreferrer" className="text-primary-600">Google Cloud Console</a> → enable the <strong>Gmail API</strong>.</li>
            <li>OAuth consent screen → External → add scope <code>gmail.send</code> → add your Gmail under <strong>Test users</strong>.</li>
            <li>Credentials → Create <strong>OAuth client ID</strong> (Web) → redirect URI — copy exactly:
              <code className="mt-1 block break-all rounded bg-white p-1 text-[11px]">{redirectUri || '(loading…)'}</code>
            </li>
            <li>Paste the Client ID + Secret below and save. One consent then signs users in and connects Gmail sending.</li>
          </ol>
          <input className="input text-xs" placeholder="Client ID (…apps.googleusercontent.com)" value={clientId} onChange={(e) => setClientId(e.target.value)} />
          <input className="input text-xs" type="password" placeholder="Client secret" value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} />
          <button onClick={save} disabled={!clientId || !clientSecret} className="btn-primary w-full text-xs">Save Google client</button>
          {msg && <p className="text-xs text-gray-700">{msg}</p>}
        </div>
      )}
    </div>
  );
}

export function GoogleCallback() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [error, setError] = useState('');

  useEffect(() => {
    const code = params.get('code');
    if (!code) {
      setError('Google login failed — please try again.');
      return;
    }
    googleConsume(code)
      .then(async (t) => {
        saveTokens(t);
        await refresh();
        navigate('/');
      })
      .catch(() => setError('Google login failed — the code expired. Please try again.'));
  }, [params]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <div className="card w-full max-w-md text-center">
        {error ? (
          <>
            <p className="text-sm text-red-700">{error}</p>
            <button onClick={() => navigate('/login')} className="btn-secondary mt-3 text-sm">Back to login</button>
          </>
        ) : (
          <p className="text-sm text-gray-600">Signing you in with Google…</p>
        )}
      </div>
    </div>
  );
}
