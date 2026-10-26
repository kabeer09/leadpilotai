import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { googleConsume } from '../lib/api';
import { useAuth } from '../auth';

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
