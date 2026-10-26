import { useState } from 'react';
import { useAuth } from '../auth';
import { login, signup, googleLoginUrl } from '../lib/api';
import { Link } from 'react-router-dom';
import { GoogleButton } from './GoogleAuth';

export function Login() {
  const { refresh } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr('');
    setLoading(true);
    try {
      const r = await login({ email, password });
      localStorage.setItem('lp_access', r.access_token);
      localStorage.setItem('lp_refresh', r.refresh_token);
      await refresh();
    } catch (ex: unknown) {
      setErr((ex as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Login failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 p-4">
      <div className="card w-full max-w-sm space-y-3">
        <h1 className="text-xl font-bold">Login</h1>
        <form onSubmit={submit} className="space-y-3">
          <input className="input" type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          <input className="input" type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          {err && <p className="text-sm text-red-600">{err}</p>}
          <button className="btn-primary w-full" disabled={loading}>{loading ? '…' : 'Login'}</button>
        </form>
        <div className="relative">
          <div className="absolute inset-0 flex items-center"><div className="w-full border-t border-gray-200" /></div>
          <div className="relative flex justify-center text-xs"><span className="bg-white px-2 text-gray-500">or</span></div>
        </div>
        <GoogleButton label="Continue with Google" />
        <p className="text-center text-sm text-gray-500">
          No account? <Link to="/signup" className="text-primary-600">Sign up</Link>
        </p>
        <p className="text-center text-sm text-gray-500">
          <Link to="/forgot" className="text-primary-600">Forgot password?</Link>
        </p>
      </div>
    </div>
  );
}

export function Signup() {
  const { refresh } = useAuth();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr('');
    setLoading(true);
    try {
      const r = await signup({ name, email, password });
      localStorage.setItem('lp_access', r.access_token);
      localStorage.setItem('lp_refresh', r.refresh_token);
      await refresh();
    } catch (ex: unknown) {
      setErr((ex as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Signup failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 p-4">
      <div className="card w-full max-w-sm space-y-3">
        <h1 className="text-xl font-bold">Sign up</h1>
        <form onSubmit={submit} className="space-y-3">
          <input className="input" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
          <input className="input" type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          <input className="input" type="password" placeholder="Password (min 8 chars)" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
          {err && <p className="text-sm text-red-600">{err}</p>}
          <button className="btn-primary w-full" disabled={loading}>{loading ? '…' : 'Create Account'}</button>
        </form>
        <div className="relative">
          <div className="absolute inset-0 flex items-center"><div className="w-full border-t border-gray-200" /></div>
          <div className="relative flex justify-center text-xs"><span className="bg-white px-2 text-gray-500">or</span></div>
        </div>
        <GoogleButton label="Sign up with Google" />
        <p className="text-center text-sm text-gray-500">
          Have an account? <Link to="/login" className="text-primary-600">Login</Link>
        </p>
      </div>
    </div>
  );
}
