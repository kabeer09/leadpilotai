import { Link } from 'react-router-dom';
import { Rocket, Globe, Users, Mail, BarChart3, ShieldCheck } from 'lucide-react';

const feats = [
  { icon: Globe, t: 'Crawl company websites', d: 'Polite, robots-aware crawler with SSRF protection extracts public emails and phones — each with its source URL.' },
  { icon: Users, t: 'Companies & leads', d: 'Searchable tables, duplicates review with preview-before-merge, CSV export, full activity timeline.' },
  { icon: Mail, t: 'Email campaigns', d: 'Dry-run by default. Preview, test to your own inbox, then double-confirm live sends with idempotency and suppression.' },
  { icon: BarChart3, t: 'Real analytics', d: 'Every number comes from your own database queries. Empty accounts show empty states, never fake stats.' },
  { icon: ShieldCheck, t: 'Private by design', d: 'bcrypt password hashing, JWT sessions, per-account data isolation enforced in the database layer.' },
];

export default function Landing() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-primary-50 to-white">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-4 py-5">
        <div className="flex items-center gap-2">
          <Rocket className="h-7 w-7 text-primary-600" />
          <span className="text-xl font-bold text-gray-900">LeadPilot Trial</span>
        </div>
        <div className="flex gap-2">
          <Link to="/login" className="btn-secondary text-sm">Log in</Link>
          <Link to="/signup" className="btn-primary text-sm">Sign up free</Link>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 pb-16">
        <div className="py-12 text-center">
          <h1 className="text-4xl font-bold text-gray-900 md:text-5xl">
            B2B lead research & email automation
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-lg text-gray-600">
            Crawl company websites, build lead lists, preview campaigns and send
            authorized test emails — all from your own private workspace.
          </p>
          <div className="mt-6 flex justify-center gap-3">
            <Link to="/signup" className="btn-primary">Start free trial</Link>
            <Link to="/login" className="btn-secondary">I have an account</Link>
          </div>
          <p className="mt-3 text-xs text-gray-500">Dry-run mode by default — nothing sends until you connect a provider and confirm.</p>
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {feats.map((f) => (
            <div key={f.t} className="card">
              <f.icon className="h-6 w-6 text-primary-600" />
              <h3 className="mt-2 font-semibold text-gray-900">{f.t}</h3>
              <p className="mt-1 text-sm text-gray-600">{f.d}</p>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
