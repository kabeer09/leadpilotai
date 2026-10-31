import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Zap, Search, ArrowRight, Check, Mail, FileSpreadsheet, Instagram,
  MessageCircle, KeyRound, Globe, Users, BarChart3, ShieldCheck,
  Sparkles, Building2, Menu, X,
} from 'lucide-react';

/* ------------------------------------------------------------------ data */

const NAV = [
  { label: 'Home', href: '#home' },
  { label: 'Features', href: '#features' },
  { label: 'Packages', href: '#packages' },
  { label: 'About', href: '#about' },
];

const PILLS = ['Trade Fairs', 'Cold Email', 'Lead Lists', 'Analytics'];

const INTEGRATIONS = [
  { icon: Mail, label: 'Gmail send' },
  { icon: KeyRound, label: 'Google login' },
  { icon: FileSpreadsheet, label: 'CSV export' },
  { icon: Instagram, label: 'Instagram' },
  { icon: MessageCircle, label: 'WhatsApp' },
];

const FEATURES = [
  {
    icon: Globe,
    title: 'Crawl & enrich',
    desc: 'Point the crawler at a trade-fair directory or any company site. It respects robots.txt, extracts public emails and phones, and keeps the source URL for every field.',
    art: 'from-indigo-200 via-sky-300 to-fuchsia-300',
    blob: 'bg-indigo-400/50',
  },
  {
    icon: Users,
    title: 'Build lead lists',
    desc: 'Searchable companies and contacts, de-duplication with preview-before-merge, lifecycle stages, and one-click CSV export into your existing CRM.',
    art: 'from-sky-200 via-blue-300 to-cyan-300',
    blob: 'bg-sky-400/50',
  },
  {
    icon: Mail,
    title: 'Send campaigns',
    desc: 'Dry-run by default. Preview every message, send a test to yourself, then double-confirm a live send with idempotency, daily caps and suppression lists.',
    art: 'from-fuchsia-200 via-purple-300 to-indigo-300',
    blob: 'bg-fuchsia-400/50',
  },
];

const GALLERY = [
  { tag: 'Homeware & gifts', note: 'Copper, brass, wood', art: 'from-[#111827] via-[#1f2937] to-[#0b1020]', accent: 'text-amber-300' },
  { tag: 'Fashion & textiles', note: 'Apparel, fabric, rugs', art: 'from-[#facc15] via-[#eab308] to-[#ca8a04]', accent: 'text-neutral-900' },
  { tag: 'Tech & electronics', note: 'Devices, accessories', art: 'from-teal-400 via-cyan-500 to-emerald-500', accent: 'text-white' },
  { tag: 'Art & design', note: 'Print, decor, studio', art: 'from-orange-500 via-rose-500 to-purple-600', accent: 'text-white' },
];

const PLANS = [
  {
    name: 'Starter', price: '€0', cadence: '14-day free trial',
    highlight: false,
    features: ['1 workspace', '500 enrichment credits', 'Dry-run campaigns', 'CSV export', 'Email support'],
    cta: 'Start free',
  },
  {
    name: 'Growth', price: '€49', cadence: 'per month',
    highlight: true,
    features: ['Unlimited leads', 'Live Gmail sending', 'Analytics dashboard', 'WhatsApp module', 'Follow-up reminders'],
    cta: 'Start free trial',
  },
  {
    name: 'Scale', price: '€129', cadence: 'per month',
    highlight: false,
    features: ['Everything in Growth', 'Multi-user team', 'API access', 'Priority onboarding', 'Dedicated support'],
    cta: 'Talk to sales',
  },
];

/* --------------------------------------------------------------- helpers */

function Logo({ dark = false }: { dark?: boolean }) {
  return (
    <Link to="/landing" className="flex items-center gap-2">
      <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary-600">
        <Zap className="h-4 w-4 text-white" />
      </span>
      <span className={`text-[17px] font-bold tracking-tight ${dark ? 'text-white' : 'text-gray-900'}`}>
        LeadPilot
      </span>
    </Link>
  );
}

/* -------------------------------------------------------------- sections */

function Header() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener('scroll', onScroll);
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <header
      className={`sticky top-0 z-40 transition-colors ${
        scrolled ? 'border-b border-gray-200/70 bg-white/80 backdrop-blur-md' : 'bg-transparent'
      }`}
    >
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4">
        <Logo />

        <nav className="hidden items-center gap-8 md:flex">
          {NAV.map((n, i) => (
            <a
              key={n.label}
              href={n.href}
              className={`text-sm font-medium transition-colors ${
                i === 0 ? 'text-gray-900' : 'text-gray-500 hover:text-gray-900'
              }`}
            >
              {n.label}
            </a>
          ))}
        </nav>

        <div className="hidden items-center gap-2 md:flex">
          <Link to="/login" className="rounded-full border border-gray-300 bg-white px-5 py-2 text-sm font-medium text-gray-900 hover:bg-gray-50">
            Sign in
          </Link>
          <Link to="/signup" className="rounded-full bg-gray-900 px-5 py-2 text-sm font-medium text-white hover:bg-gray-800">
            Free Trial
          </Link>
        </div>

        <button
          className="md:hidden"
          onClick={() => setOpen((v) => !v)}
          aria-label="Toggle menu"
        >
          {open ? <X className="h-6 w-6 text-gray-900" /> : <Menu className="h-6 w-6 text-gray-900" />}
        </button>
      </div>

      {open && (
        <div className="border-t border-gray-200/70 bg-white px-4 py-3 md:hidden">
          <nav className="flex flex-col gap-1">
            {NAV.map((n) => (
              <a key={n.label} href={n.href} onClick={() => setOpen(false)} className="rounded-lg px-2 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50">
                {n.label}
              </a>
            ))}
          </nav>
          <div className="mt-2 flex gap-2">
            <Link to="/login" className="flex-1 rounded-full border border-gray-300 px-4 py-2 text-center text-sm font-medium text-gray-900">Sign in</Link>
            <Link to="/signup" className="flex-1 rounded-full bg-gray-900 px-4 py-2 text-center text-sm font-medium text-white">Free Trial</Link>
          </div>
        </div>
      )}
    </header>
  );
}

function Hero() {
  const navigate = useNavigate();
  const [q, setQ] = useState('');

  return (
    <section id="home" className="relative overflow-hidden">
      {/* soft gradient blobs */}
      <div className="pointer-events-none absolute inset-0 -z-10">
        <div className="absolute -right-24 -top-24 h-[420px] w-[420px] rounded-full bg-pink-300/50 blur-3xl" />
        <div className="absolute right-10 top-10 h-[320px] w-[320px] rounded-full bg-fuchsia-300/40 blur-3xl" />
        <div className="absolute -left-24 top-40 h-[360px] w-[360px] rounded-full bg-indigo-300/40 blur-3xl" />
        <div className="absolute inset-x-0 top-0 h-40 bg-gradient-to-b from-purple-50/60 to-transparent" />
      </div>

      <div className="mx-auto max-w-6xl px-4 pb-20 pt-16 text-center md:pt-24">
        <div className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white/70 px-3 py-1 text-xs font-medium text-gray-600 backdrop-blur">
          <Sparkles className="h-3.5 w-3.5 text-primary-600" />
          B2B lead research &amp; email automation
        </div>

        <h1 className="mx-auto mt-6 max-w-3xl text-4xl font-bold tracking-tight text-gray-900 md:text-6xl">
          Find your next
          <span className="bg-gradient-to-r from-primary-600 to-fuchsia-500 bg-clip-text text-transparent"> customer </span>
          on autopilot
        </h1>

        <p className="mx-auto mt-5 max-w-2xl text-lg text-gray-600">
          Crawl trade-fair directories and company websites, build qualified lead lists,
          and send personalized campaigns — all from one private workspace.
        </p>

        {/* search */}
        <form
          onSubmit={(e) => { e.preventDefault(); navigate('/signup'); }}
          className="mx-auto mt-8 flex max-w-2xl items-center gap-2 rounded-full border border-gray-200 bg-white p-2 pl-5 shadow-[0_8px_30px_rgba(0,0,0,0.06)]"
        >
          <Search className="h-5 w-5 shrink-0 text-gray-400" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search 50+ trade fairs and industries…"
            className="w-full bg-transparent text-sm text-gray-900 placeholder-gray-400 focus:outline-none"
          />
          <button
            type="submit"
            className="shrink-0 rounded-full bg-gray-900 px-6 py-2.5 text-sm font-medium text-white hover:bg-gray-800"
          >
            Search
          </button>
        </form>

        {/* pills */}
        <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
          {PILLS.map((p) => (
            <button
              key={p}
              onClick={() => setQ(p)}
              className="rounded-full border border-gray-200 bg-white/70 px-5 py-2 text-sm font-medium text-gray-700 backdrop-blur transition hover:bg-white"
            >
              {p}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

function Integrations() {
  return (
    <div className="border-y border-gray-100 bg-white">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-center gap-x-12 gap-y-6 px-4 py-8">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">Works with</span>
        {INTEGRATIONS.map((it) => (
          <div key={it.label} className="flex items-center gap-2 text-gray-400 transition hover:text-gray-600">
            <it.icon className="h-5 w-5" />
            <span className="text-sm font-semibold">{it.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Features() {
  return (
    <section id="features" className="mx-auto max-w-6xl px-4 py-20">
      <div className="mx-auto max-w-2xl text-center">
        <h2 className="text-3xl font-bold tracking-tight text-gray-900 md:text-4xl">
          Let your pipeline do the talking
        </h2>
        <p className="mt-3 text-lg text-gray-600">
          Everything you need to turn a trade-show list into booked conversations —
          without leaving your workspace.
        </p>
      </div>

      <div className="mt-12 grid grid-cols-1 gap-6 md:grid-cols-3">
        {FEATURES.map((f) => (
          <div key={f.title} className="group overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-[0_1px_3px_rgba(0,0,0,0.04)] transition hover:shadow-[0_12px_40px_rgba(0,0,0,0.08)]">
            {/* abstract art header */}
            <div className={`relative h-44 overflow-hidden bg-gradient-to-br ${f.art}`}>
              <div className={`absolute -left-6 -top-6 h-32 w-32 rounded-full ${f.blob} blur-2xl`} />
              <div className="absolute -bottom-10 right-4 h-36 w-36 rounded-full bg-white/40 blur-2xl" />
              <div className="absolute inset-0 flex items-center justify-center">
                <f.icon className="h-14 w-14 text-white/90 drop-shadow" />
              </div>
            </div>
            <div className="p-5">
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-bold text-gray-900">{f.title}</h3>
                <ArrowRight className="h-5 w-5 text-gray-400 transition group-hover:translate-x-1 group-hover:text-gray-900" />
              </div>
              <p className="mt-2 text-sm leading-relaxed text-gray-600">{f.desc}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function Gallery() {
  return (
    <section className="mx-auto max-w-6xl px-4 py-16">
      <div className="mx-auto max-w-2xl text-center">
        <h2 className="text-3xl font-bold tracking-tight text-gray-900 md:text-4xl">
          Built for modern commerce brands
        </h2>
        <p className="mt-3 text-lg text-gray-600">
          From homeware to fashion, LeadPilot helps you reach the buyers who actually stock your products.
        </p>
      </div>

      <div className="mt-12 grid grid-cols-2 gap-4 md:grid-cols-4">
        {GALLERY.map((g) => (
          <div key={g.tag} className={`relative aspect-[3/4] overflow-hidden rounded-2xl bg-gradient-to-br ${g.art} p-4 shadow-lg`}>
            <div className="absolute -right-8 -top-8 h-24 w-24 rounded-full bg-white/20 blur-2xl" />
            <div className="absolute -bottom-10 -left-6 h-28 w-28 rounded-full bg-white/10 blur-2xl" />
            <div className="relative flex h-full flex-col justify-end">
              <span className={`text-[11px] font-semibold uppercase tracking-wider ${g.accent} opacity-80`}>
                LeadPilot
              </span>
              <h3 className={`mt-1 text-lg font-bold leading-tight ${g.accent}`}>{g.tag}</h3>
              <p className={`mt-1 text-xs ${g.accent} opacity-80`}>{g.note}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function Packages() {
  return (
    <section id="packages" className="mx-auto max-w-6xl px-4 py-20">
      <div className="mx-auto max-w-2xl text-center">
        <h2 className="text-3xl font-bold tracking-tight text-gray-900 md:text-4xl">Simple packages</h2>
        <p className="mt-3 text-lg text-gray-600">Start free. Upgrade when you are ready to send at scale.</p>
      </div>

      <div className="mt-12 grid grid-cols-1 gap-6 md:grid-cols-3">
        {PLANS.map((p) => (
          <div
            key={p.name}
            className={`relative rounded-2xl border p-6 ${
              p.highlight
                ? 'border-gray-900 bg-gray-900 text-white shadow-xl'
                : 'border-gray-200 bg-white text-gray-900'
            }`}
          >
            {p.highlight && (
              <span className="absolute -top-3 left-6 rounded-full bg-lime-400 px-3 py-1 text-xs font-semibold text-gray-900">
                Most popular
              </span>
            )}
            <h3 className={`text-sm font-semibold ${p.highlight ? 'text-white/70' : 'text-gray-500'}`}>{p.name}</h3>
            <div className="mt-2 flex items-baseline gap-1">
              <span className="text-4xl font-bold">{p.price}</span>
              <span className={`text-sm ${p.highlight ? 'text-white/60' : 'text-gray-500'}`}>{p.cadence}</span>
            </div>

            <ul className="mt-5 space-y-2.5">
              {p.features.map((f) => (
                <li key={f} className="flex items-start gap-2 text-sm">
                  <Check className={`mt-0.5 h-4 w-4 shrink-0 ${p.highlight ? 'text-lime-400' : 'text-primary-600'}`} />
                  <span className={p.highlight ? 'text-white/85' : 'text-gray-700'}>{f}</span>
                </li>
              ))}
            </ul>

            <Link
              to="/signup"
              className={`mt-6 block rounded-full px-4 py-2.5 text-center text-sm font-medium transition ${
                p.highlight
                  ? 'bg-white text-gray-900 hover:bg-gray-100'
                  : 'bg-gray-900 text-white hover:bg-gray-800'
              }`}
            >
              {p.cta}
            </Link>
          </div>
        ))}
      </div>
      <p className="mt-6 text-center text-xs text-gray-400">
        *Sample pricing — edit the PLANS array in <code>src/pages/Landing.tsx</code> to match your real plans.
      </p>
    </section>
  );
}

function About() {
  return (
    <section id="about" className="mx-auto max-w-6xl px-4 py-16">
      <div className="grid grid-cols-1 items-center gap-10 rounded-3xl bg-gradient-to-br from-primary-50 to-white p-8 md:grid-cols-2 md:p-12">
        <div>
          <h2 className="text-3xl font-bold tracking-tight text-gray-900">Private by design</h2>
          <p className="mt-3 text-gray-600">
            LeadPilot is a single-tenant workspace: your data stays in your database. Sends are
            dry-run until you connect a provider and explicitly confirm, so nothing goes out by accident.
          </p>
          <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
            {[
              { icon: ShieldCheck, t: 'Per-account isolation' },
              { icon: Building2, t: 'Source URL on every field' },
              { icon: BarChart3, t: 'Real, query-backed analytics' },
              { icon: Users, t: 'De-dupe with preview' },
            ].map((b) => (
              <div key={b.t} className="flex items-center gap-2 text-sm font-medium text-gray-700">
                <b.icon className="h-4 w-4 text-primary-600" />
                {b.t}
              </div>
            ))}
          </div>
        </div>
        <div className="relative">
          <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-lg">
            <div className="flex items-center gap-2 border-b border-gray-100 pb-3">
              <span className="h-3 w-3 rounded-full bg-red-400" />
              <span className="h-3 w-3 rounded-full bg-yellow-400" />
              <span className="h-3 w-3 rounded-full bg-green-400" />
              <span className="ml-2 text-xs text-gray-400">workspace.leadpilot</span>
            </div>
            <div className="mt-4 space-y-3">
              <div className="h-3 w-3/4 rounded bg-gray-100" />
              <div className="h-3 w-1/2 rounded bg-gray-100" />
              <div className="grid grid-cols-3 gap-2 pt-2">
                <div className="h-16 rounded-lg bg-primary-50" />
                <div className="h-16 rounded-lg bg-lime-100" />
                <div className="h-16 rounded-lg bg-gray-100" />
              </div>
              <div className="h-24 rounded-lg bg-gray-50" />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function CTA() {
  return (
    <section className="mx-auto max-w-6xl px-4 pb-20">
      <div className="relative overflow-hidden rounded-3xl bg-gray-900 px-8 py-14 text-center">
        <div className="pointer-events-none absolute -left-10 -top-10 h-48 w-48 rounded-full bg-fuchsia-500/30 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-16 right-0 h-56 w-56 rounded-full bg-primary-500/30 blur-3xl" />
        <h2 className="relative text-3xl font-bold tracking-tight text-white md:text-4xl">
          Ready to fill your pipeline?
        </h2>
        <p className="relative mx-auto mt-3 max-w-xl text-white/70">
          Start a free trial — dry-run by default, nothing sends until you say so.
        </p>
        <div className="relative mt-7 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link to="/signup" className="rounded-full bg-white px-7 py-3 text-sm font-semibold text-gray-900 hover:bg-gray-100">
            Start free trial
          </Link>
          <Link to="/login" className="rounded-full border border-white/30 px-7 py-3 text-sm font-semibold text-white hover:bg-white/10">
            I have an account
          </Link>
        </div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="border-t border-gray-100 bg-white">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-4 py-8 md:flex-row">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary-600">
            <Zap className="h-3.5 w-3.5 text-white" />
          </span>
          <span className="text-sm font-bold text-gray-900">LeadPilot</span>
          <span className="ml-2 rounded-md bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">trial</span>
        </div>
        <nav className="flex flex-wrap items-center justify-center gap-6 text-sm text-gray-500">
          <a href="#features" className="hover:text-gray-900">Features</a>
          <a href="#packages" className="hover:text-gray-900">Packages</a>
          <a href="#about" className="hover:text-gray-900">About</a>
          <Link to="/login" className="hover:text-gray-900">Sign in</Link>
        </nav>
        <p className="text-xs text-gray-400">© {new Date().getFullYear()} LeadPilot. All rights reserved.</p>
      </div>
    </footer>
  );
}

/* ------------------------------------------------------------------ page */

export default function Landing() {
  return (
    <div className="min-h-screen bg-white">
      <Header />
      <main>
        <Hero />
        <Integrations />
        <Features />
        <Gallery />
        <Packages />
        <About />
        <CTA />
      </main>
      <Footer />
    </div>
  );
}
