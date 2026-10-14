import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { requestReset, confirmReset } from '../lib/api';

export function Forgot() {
  const [email, setEmail] = useState('');
  const [done, setDone] = useState(false);
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <form
        className="card w-full max-w-md"
        onSubmit={async (e) => {
          e.preventDefault();
          await requestReset(email);
          setDone(true);
        }}
      >
        <h1 className="text-xl font-bold">Reset password</h1>
        {done ? (
          <p className="mt-3 text-sm text-gray-600">If the address exists, a reset link was sent. Check your inbox.</p>
        ) : (
          <>
            <label className="label mt-4">Email<input className="input mt-1" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            <button className="btn-primary mt-4 w-full">Send reset link</button>
          </>
        )}
        <p className="mt-3 text-center text-sm"><Link to="/login" className="text-primary-600">Back to login</Link></p>
      </form>
    </div>
  );
}

export function Reset() {
  const [params] = useSearchParams();
  const [password, setPassword] = useState('');
  const [msg, setMsg] = useState('');
  const navigate = useNavigate();
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <form
        className="card w-full max-w-md"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            await confirmReset({ token: params.get('token') || '', new_password: password });
            setMsg('Password updated — please log in.');
            setTimeout(() => navigate('/login'), 1500);
          } catch {
            setMsg('Invalid or expired reset link.');
          }
        }}
      >
        <h1 className="text-xl font-bold">Choose a new password</h1>
        {msg && <p className="mt-3 rounded-lg bg-blue-50 p-2 text-sm text-blue-700">{msg}</p>}
        <label className="label mt-4">New password (min 8)<input className="input mt-1" type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <button className="btn-primary mt-4 w-full">Update password</button>
      </form>
    </div>
  );
}
