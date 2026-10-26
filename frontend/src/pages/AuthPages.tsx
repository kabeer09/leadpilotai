import { useState } from 'react';
import { useAuth } from '../auth';
import { login, signup, googleLoginUrl, sendOTP, verifyOTP } from '../lib/api';
import { Link } from 'react-router-dom';

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
      <form onSubmit={submit} className="card w-full max-w-sm space-y-3">
        <h1 className="text-xl font-bold">Login</h1>
        <input className="input" type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <input className="input" type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        {err && <p className="text-sm text-red-600">{err}</p>}
        <button className="btn-primary w-full" disabled={loading}>{loading ? '…' : 'Login'}</button>
        <p className="text-center text-sm text-gray-500">
          No account? <Link to="/signup" className="text-primary-600">Sign up</Link>
        </p>
        <p className="text-center text-sm text-gray-500">
          <Link to="/forgot" className="text-primary-600">Forgot password?</Link>
        </p>
      </form>
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
  const [otpSent, setOtpSent] = useState(false);
  const [otp, setOtp] = useState('');

  const sendOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr('');
    setLoading(true);
    try {
      await sendOTP({ email });
      setOtpSent(true);
    } catch (ex: unknown) {
      setErr((ex as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Failed to send OTP.');
    } finally {
      setLoading(false);
    }
  };

  const verifyOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr('');
    setLoading(true);
    try {
      const r = await verifyOTP({ email, otp });
      if (r.access_token) {
        localStorage.setItem('lp_access', r.access_token);
        localStorage.setItem('lp_refresh', r.refresh_token);
        await refresh();
      } else {
        setErr('Verification failed.');
      }
    } catch (ex: unknown) {
      setErr((ex as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Verification failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 p-4">
      <div className="card w-full max-w-sm space-y-3">
        <h1 className="text-xl font-bold">Sign up</h1>
        {!otpSent ? (
          <form onSubmit={sendOtp} className="space-y-3">
            <input className="input" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
            <input className="input" type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            <button className="btn-primary w-full" disabled={loading}>{loading ? '…' : 'Send Verification Code'}</button>
          </form>
        ) : (
          <form onSubmit={verifyOtp} className="space-y-3">
            <p className="text-sm text-gray-600">Enter the 6-digit code sent to {email}</p>
            <input className="input" type="text" placeholder="OTP Code" value={otp} onChange={(e) => setOtp(e.target.value)} required maxLength={6} />
            <button className="btn-primary w-full" disabled={loading}>{loading ? '…' : 'Verify & Create Account'}</button>
            <button type="button" className="btn-secondary w-full text-sm" onClick={() => setOtpSent(false)}>Back</button>
          </form>
        )}
        {err && <p className="text-sm text-red-600">{err}</p>}
        <p className="text-center text-sm text-gray-500">
          Have an account? <Link to="/login" className="text-primary-600">Login</Link>
        </p>
      </div>
    </div>
  );
}
