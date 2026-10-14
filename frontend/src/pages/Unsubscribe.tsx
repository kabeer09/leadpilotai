import { useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import axios from 'axios';

export default function Unsubscribe() {
  const [params] = useSearchParams();
  const [email, setEmail] = useState(params.get('email') || '');
  const [done, setDone] = useState(false);
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <form
        className="card w-full max-w-md"
        onSubmit={async (e) => {
          e.preventDefault();
          await axios.post('/api/unsubscribe', { email });
          setDone(true);
        }}
      >
        <h1 className="text-xl font-bold">Unsubscribe</h1>
        {done ? (
          <p className="mt-3 text-sm text-gray-600">Done — {email} will receive no further campaign emails. <Link to="/" className="text-primary-600">Home</Link></p>
        ) : (
          <>
            <p className="mt-1 text-sm text-gray-500">Enter the address to suppress from all future campaigns.</p>
            <input className="input mt-3" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            <button className="btn-primary mt-3 w-full">Unsubscribe</button>
          </>
        )}
      </form>
    </div>
  );
}
