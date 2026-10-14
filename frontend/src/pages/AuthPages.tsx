import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Rocket } from 'lucide-react';
import { login, signup } from '../lib/api';
import { useAuth } from '../auth';

function saveTokens(t: { access_token: string; refresh_token: string }) {
  localStorage.setItem('lp_access', t.access_token);
  localStorage.setItem('lp_refresh', t.refresh_token);
}

export function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const { refresh } = useAuth();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      saveTokens(await login({ email, password }));
      await refresh();
      navigate('/');
    } catch (err: unknown) {
      const msg = (err as { response?: { status?: number; data?: { detail?: string } } });
      setError(msg.response?.data?.detail || (msg.response?.status === 429 ? 'Too many attempts — wait a minute' : 'Login failed'));
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <form onSubmit={submit} className="card w-full max-w-md">
        <div className="flex items-center gap-2">
          <Rocket className="h-6 w-6 text-primary-600" />
          <h1 className="text-xl font-bold">Log in</h1>
        </div>
        {error && <p className="mt-3 rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</p>}
        <label className="label mt-4">Email<input className="input mt-1" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label className="label mt-3">Password<input className="input mt-1" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <button className="btn-primary mt-4 w-full">Log in</button>
        <div className="mt-3 flex justify-between text-sm">
          <Link to="/signup" className="text-primary-600">Create account</Link>
          <Link to="/forgot" className="text-primary-600">Forgot password?</Link>
        </div>
      </form>
    </div>
  );
}

export function Signup() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const { refresh } = useAuth();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      saveTokens(await signup({ email, password, name: name || undefined }));
      await refresh();
      navigate('/');
    } catch (err: unknown) {
      const msg = (err as { response?: { status?: number; data?: { detail?: string } } });
      setError(msg.response?.data?.detail || (msg.response?.status === 429 ? 'Too many attempts — wait a minute' : 'Signup failed'));
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <form onSubmit={submit} className="card w-full max-w-md">
        <div className="flex items-center gap-2">
          <Rocket className="h-6 w-6 text-primary-600" />
          <h1 className="text-xl font-bold">Create your account</h1>
        </div>
        <p className="mt-1 text-xs text-gray-500">Your data stays in your own private workspace.</p>
        {error && <p className="mt-3 rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</p>}
        <label className="label mt-4">Name (optional)<input className="input mt-1" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="label mt-3">Email<input className="input mt-1" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label className="label mt-3">Password (min 8 characters)<input className="input mt-1" type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <button className="btn-primary mt-4 w-full">Sign up</button>
        <p className="mt-3 text-center text-sm"><Link to="/login" className="text-primary-600">Already have an account?</Link></p>
      </form>
    </div>
  );
}
