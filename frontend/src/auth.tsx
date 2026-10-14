import { createContext, useContext, useEffect, useState, ReactNode } from 'react';
import { me, logout as apiLogout } from './lib/api';

interface User {
  id: number;
  email: string;
  name: string | null;
  is_admin: boolean;
  email_verified: boolean;
}

const AuthCtx = createContext<{ user: User | null; loading: boolean; refresh: () => Promise<void>; signOut: () => Promise<void> }>({
  user: null,
  loading: true,
  refresh: async () => {},
  signOut: async () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    if (!localStorage.getItem('lp_access')) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      setUser(await me());
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const signOut = async () => {
    try {
      await apiLogout();
    } catch {}
    localStorage.removeItem('lp_access');
    localStorage.removeItem('lp_refresh');
    setUser(null);
    window.location.href = '/login';
  };

  return <AuthCtx.Provider value={{ user, loading, refresh, signOut }}>{children}</AuthCtx.Provider>;
}

export const useAuth = () => useContext(AuthCtx);
