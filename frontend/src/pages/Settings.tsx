import { useState, useEffect } from 'react';
import { useAuth } from '../auth';
import api, { health, listSuppressions, gmailStatus } from '../lib/api';

export default function Settings() {
  const { user, refresh } = useAuth();
  const [sys, setSys] = useState<{ email_provider: string; provider_configured: boolean; warnings: string[]; database: string } | null>(null);
  const [supp, setSupp] = useState<{ id: number; value: string; reason: string }[]>([]);
  const [verifyMsg, setVerifyMsg] = useState('');
  const [users, setUsers] = useState<{ id: number; email: string; is_active: boolean; is_admin: boolean }[]>([]);
  const [gmail, setGmail] = useState<{ connected: boolean; provider_mode: string } | null>(null);

  useEffect(() => {
    health().then(setSys).catch(() => {});
    listSuppressions().then(setSupp).catch(() => {});
    gmailStatus().then(setGmail).catch(() => {});
    refresh();
  }, []);

  useEffect(() => {
    if (user?.is_admin) api.get('/admin/users').then((r) => setUsers(r.data)).catch(() => {});
  }, [user]);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
      <div className="card">
        <h2 className="font-semibold">Account</h2>
        <dl className="mt-2 space-y-1 text-sm">
          <div><dt className="inline text-gray-500">Email: </dt><dd className="inline font-medium">{user?.email}</dd></div>
          <div><dt className="inline text-gray-500">Verified: </dt><dd className="inline">{user?.email_verified ? 'Yes — live sending unlocked' : 'No — verify to unlock live sending (dry-run still works)'}</dd></div>
          <div><dt className="inline text-gray-500">Role: </dt><dd className="inline">{user?.is_admin ? 'Admin (first account)' : 'Member'}</dd></div>
          <div><dt className="inline text-gray-500">WhatsApp: </dt><dd className="inline font-medium">{user?.whatsapp_number || 'Not set'}</dd></div>
        </dl>
        <div className="mt-3 flex gap-2">
          <input
            className="input flex-1 py-1.5 text-sm"
            placeholder="+918218251238"
            id="wa-number"
          />
          <button
            className="btn-secondary text-sm"
            onClick={async () => {
              const el = document.getElementById('wa-number') as HTMLInputElement;
              if (!el.value) return;
              await api.patch('/auth/me', { whatsapp_number: el.value });
              refresh();
            }}
          >
            Save WhatsApp
          </button>
        </div>
      </div>
      <div className="card">
        <h2 className="font-semibold">Email provider (server-side)</h2>
        <p className="mt-1 text-sm text-gray-600">
          Current: <strong>{sys?.email_provider || '…'}</strong> — {sys?.provider_configured ? 'ready' : 'not fully configured'}.
          Provider credentials live only in the server <code>.env</code> (<code>EMAIL_PROVIDER</code> = dryrun|smtp|resend),
          never in the browser. See README → provider setup.
        </p>
        <p className="mt-2 text-sm">
          Gmail: {gmail ? (gmail.connected
            ? <span className="badge bg-green-100 text-green-800">connected — campaigns can send via your Gmail</span>
            : <span className="badge bg-yellow-100 text-yellow-800">not connected — use “Continue with Google” on the login page</span>)
            : 'checking…'}
        </p>
        {sys && sys.warnings.length > 0 && (
          <ul className="mt-2 space-y-1 text-xs text-yellow-700">
            {sys.warnings.map((w, i) => <li key={i}>• {w}</li>)}
          </ul>
        )}
      </div>
      {!user?.email_verified && (
        <div className="card">
          <h2 className="font-semibold">Verify your email</h2>
          <p className="mt-1 text-sm text-gray-600">Live sending unlocks after verification. In dry-run mode (no mailer), fetch your own verification token below.</p>
          <button
            className="btn-secondary mt-2 text-sm"
            onClick={async () => {
              const s = await api.get('/auth/verification-status').then((r) => r.data);
              if (s.verified) {
                setVerifyMsg('Already verified.');
              } else if (s.token) {
                await api.get('/auth/verify', { params: { token: s.token } });
                setVerifyMsg('Verified! Live sending unlocked (once a provider is configured).');
                refresh();
              } else {
                setVerifyMsg(s.hint || 'Check your email for the verification link.');
              }
            }}
          >
            Verify now
          </button>
          {verifyMsg && <p className="mt-2 text-sm text-gray-700">{verifyMsg}</p>}
        </div>
      )}
      {user?.is_admin && (
        <div className="card">
          <h2 className="font-semibold">Admin — accounts ({users.length})</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {users.map((u) => (
              <li key={u.id} className="flex items-center gap-2">
                <span className={u.is_active ? '' : 'text-gray-400 line-through'}>{u.email}</span>
                {u.is_admin && <span className="badge bg-blue-100 text-blue-700">admin</span>}
                {!u.is_active && <span className="badge bg-red-100 text-red-700">suspended</span>}
                <button
                  className="btn-secondary ml-auto text-xs"
                  onClick={async () => {
                    await api.post(`/admin/users/${u.id}/suspend`, { active: !u.is_active });
                    setUsers(await api.get('/admin/users').then((r) => r.data));
                  }}
                >
                  {u.is_active ? 'Suspend' : 'Unsuspend'}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="card">
        <h2 className="font-semibold">Suppression list ({supp.length})</h2>
        <p className="mt-1 text-xs text-gray-500">Unsubscribed/bounced addresses. They are excluded from every batch automatically, even with resend allowed.</p>
        <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto text-sm">
          {supp.map((s) => <li key={s.id}>{s.value} <span className="text-gray-400">({s.reason})</span></li>)}
          {supp.length === 0 && <li className="text-sm italic text-gray-400">Empty.</li>}
        </ul>
      </div>
    </div>
  );
}
