import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Zap, Search, ArrowRight, Check, Mail, FileSpreadsheet, Instagram,
  MessageCircle, KeyRound, Globe, Users, BarChart3, ShieldCheck,
  Sparkles, Building2, Menu, X, Lock, MapPin, Loader2, ExternalLink,
} from 'lucide-react';
import { directoryMeta, directorySearch, directoryCompany } from '../lib/api';

/* ------------------------------------------------------------------ data */

const NAV = [
  { label: 'Home', href: '#home' },
  { label: 'Directory', href: '#directory' },
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
    name: 'Starter', price: '€0', cadence: 'directory free for a limited time',
    highlight: false,
    features: ['Public buyer/product directory', '40 free searches per day', 'Fair & country filters', 'Sign up to reveal contacts', 'Founding-member pricing locked at €0'],
    cta: 'Search free now',
  },
  {
    name: 'Growth', price: '€49', cadence: 'per month · founding rate',
    highlight: true,
    features: ['Unlimited lead research', 'Live Gmail sending', 'Contact reveal & export', 'Analytics dashboard', 'Follow-up reminders'],
    cta: 'Start free trial',
  },
  {
    name: 'Scale', price: '€129', cadence: 'per month · founding rate',
    highlight: false,
    features: ['Everything in Growth', 'Multi-user team', 'API access', 'Priority onboarding', 'Dedicated support'],
    cta: 'Talk to sales',
  },
];

/* --------------------------------------------------- directory tool types */

type DirMeta = {
  total: number;
  with_email: number;
  with_phone: number;
  by_fair: { id: string; label: string; count: number }[];
  fairs: { id: string; label: string }[];
  countries: string[];
  free_per_day: number;
  updated_at: string | null;
  disclaimer: string;
  locked_note: string;
};

type DirItem = {
  id: number;
  name: string;
  city: string | null;
  country: string | null;
  category: string | null;
  product_summary: string;
  products: { name: string }[];
  fair: string | null;
  hall: string | null;
  booth: string | null;
  source_url: string | null;
  has_email: boolean;
  has_phone: boolean;
};

type DirSearch = {
  record_type: string;
  disclaimer: string;
  locked_note: string;
  free: { per_day: number; remaining: number };
  total: number;
  page: number;
  page_size: number;
  items: DirItem[];
};

type DirReveal = {
  company: {
    id: number;
    name: string;
    website: string | null;
    fair: string | null;
    source_url: string | null;
    product_summary: string;
    hall: string | null;
    booth: string | null;
    country: string | null;
    city: string | null;
  };
  contacts: {
    kind: string;
    value: string;
    source_url: string | null;
    verification: string;
    is_primary: boolean;
  }[];
  disclaimer: string;
  evidence_note: string;
};

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
  const [q, setQ] = useState('');

  const submit = (value?: string) => {
    const query = (value ?? q).trim();
    // Hand the query to the directory tool below and scroll to it.
    window.dispatchEvent(new CustomEvent('lp:search', { detail: { q: query } }));
    document.getElementById('directory')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

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
          Search thousands of trade-fair exhibitors by product or company — free for a limited time —
          then build qualified lead lists and send personalized campaigns from one private workspace.
        </p>

        {/* search */}
        <form
          onSubmit={(e) => { e.preventDefault(); submit(); }}
          className="mx-auto mt-8 flex max-w-2xl items-center gap-2 rounded-full border border-gray-200 bg-white p-2 pl-5 shadow-[0_8px_30px_rgba(0,0,0,0.06)]"
        >
          <Search className="h-5 w-5 shrink-0 text-gray-400" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search products, companies or trade fairs…"
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
              onClick={() => { setQ(p); submit(p); }}
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

/* ---------------------------------------------------- directory tool */

function DirectoryTool() {
  const navigate = useNavigate();
  const [meta, setMeta] = useState<DirMeta | null>(null);
  const [q, setQ] = useState('');
  const [fair, setFair] = useState('');
  const [country, setCountry] = useState('');
  const [res, setRes] = useState<DirSearch | null>(null);
  const [limitMsg, setLimitMsg] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [page, setPage] = useState(1);
  const [revealed, setRevealed] = useState<Record<number, DirReveal>>({});
  const [revealBusy, setRevealBusy] = useState<number | null>(null);

  // public totals for the hero counter / filter dropdowns
  useEffect(() => {
    directoryMeta()
      .then((m: DirMeta) => setMeta(m))
      .catch(() => setMeta(null));
  }, []);

  const run = async (opts?: { q?: string; fair?: string; country?: string; page?: number }) => {
    const query = (opts?.q ?? q).trim();
    const fairV = opts?.fair ?? fair;
    const countryV = opts?.country ?? country;
    const pageV = opts?.page ?? 1;
    if (query.length === 1) {
      setNotice('Keep the search to 2+ characters.');
      return;
    }
    if (!query && !fairV && !countryV) {
      setNotice('Type a product or company name, or pick a fair / country filter.');
      return;
    }
    setBusy(true);
    setNotice('');
    setLimitMsg('');
    try {
      const params: Record<string, string | number> = { page: pageV };
      if (query) params.q = query;
      if (fairV) params.fair = fairV;
      if (countryV) params.country = countryV;
      const data: DirSearch = await directorySearch(params);
      setRes(data);
      setPage(pageV);
    } catch (err) {
      const e = err as { response?: { status?: number; data?: { detail?: string } } };
      const status = e.response?.status;
      const detail = typeof e.response?.data?.detail === 'string' ? e.response.data.detail : '';
      if (status === 429) {
        setLimitMsg(detail || 'Free daily search limit reached — create a free account to keep searching.');
        setRes(null);
      } else if (status === 422) {
        setNotice(detail || 'That search was not accepted.');
      } else {
        setNotice('Something went wrong — please try again.');
      }
    } finally {
      setBusy(false);
    }
  };

  // hero search + pill clicks arrive as a CustomEvent
  useEffect(() => {
    const onSearch = (e: Event) => {
      const detail = (e as CustomEvent<{ q?: string }>).detail || {};
      const query = (detail.q || '').trim();
      setQ(query);
      void run({ q: query, page: 1 });
    };
    window.addEventListener('lp:search', onSearch);
    return () => window.removeEventListener('lp:search', onSearch);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, fair, country]);

  // contact reveal — the conversion gate: 401 sends the guest to signup
  const reveal = async (id: number) => {
    setRevealBusy(id);
    try {
      const data: DirReveal = await directoryCompany(id);
      setRevealed((r) => ({ ...r, [id]: data }));
    } catch (err) {
      const e = err as { response?: { status?: number; data?: { detail?: string } } };
      if (e.response?.status === 401) {
        navigate('/signup');
      } else {
        setNotice(
          typeof e.response?.data?.detail === 'string'
            ? e.response.data.detail
            : 'Could not reveal this company — please try again.'
        );
      }
    } finally {
      setRevealBusy(null);
    }
  };

  const total = meta ? meta.total.toLocaleString() : '—';
  const pages = res ? Math.max(1, Math.ceil(res.total / res.page_size)) : 1;

  return (
    <section id="directory" className="mx-auto max-w-6xl scroll-mt-20 px-4 py-16">
      <div className="mx-auto max-w-3xl text-center">
        <div className="inline-flex items-center gap-2 rounded-full border border-lime-200 bg-lime-50 px-3 py-1 text-xs font-semibold text-lime-800">
          <Sparkles className="h-3.5 w-3.5" />
          Free for a limited time
        </div>
        <h2 className="mt-4 text-3xl font-bold tracking-tight text-gray-900 md:text-4xl">
          Search the global exhibitor directory
        </h2>
        <p className="mt-3 text-lg text-gray-600">
          {meta
            ? `${total} exhibitor profiles from crawled trade fairs — by product, company, fair or country.`
            : 'Exhibitor profiles from crawled trade fairs — by product, company, fair or country.'}
        </p>
      </div>

      {/* search controls */}
      <div className="mx-auto mt-8 max-w-4xl rounded-2xl border border-gray-200 bg-white p-4 shadow-[0_8px_30px_rgba(0,0,0,0.05)]">
        <form
          onSubmit={(e) => { e.preventDefault(); void run({ page: 1 }); }}
          className="flex flex-col gap-3 md:flex-row"
        >
          <div className="flex flex-1 items-center gap-2 rounded-full border border-gray-200 bg-gray-50 px-4 py-2.5">
            <Search className="h-4 w-4 shrink-0 text-gray-400" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="e.g. ceramic tiles, lighting, packaging…"
              className="w-full bg-transparent text-sm text-gray-900 placeholder-gray-400 focus:outline-none"
            />
          </div>
          <select
            value={fair}
            onChange={(e) => setFair(e.target.value)}
            className="rounded-full border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-700 focus:outline-none"
          >
            <option value="">All trade fairs</option>
            {(meta?.fairs || []).map((f) => (
              <option key={f.id} value={f.id}>{f.label}</option>
            ))}
          </select>
          <select
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            className="rounded-full border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-700 focus:outline-none"
          >
            <option value="">All countries</option>
            {(meta?.countries || []).map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <button
            type="submit"
            disabled={busy}
            className="flex items-center justify-center gap-2 rounded-full bg-gray-900 px-6 py-2.5 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-60"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            Search
          </button>
        </form>

        {/* free-limit banner */}
        {limitMsg ? (
          <div className="mt-4 flex flex-col items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 sm:flex-row sm:items-center">
            <span className="flex-1">{limitMsg}</span>
            <Link
              to="/signup"
              className="shrink-0 rounded-full bg-amber-900 px-4 py-1.5 text-xs font-semibold text-white hover:bg-amber-800"
            >
              Create free account
            </Link>
          </div>
        ) : null}

        {notice ? (
          <p className="mt-3 text-sm text-gray-500">{notice}</p>
        ) : null}

        {res ? (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-gray-100 pt-4 text-xs text-gray-500">
            <span>
              <b className="text-gray-900">{res.total.toLocaleString()}</b> results
              {res.free ? (
                <> · <b className="text-gray-900">{res.free.remaining}</b> of {res.free.per_day} free searches left today</>
              ) : null}
            </span>
            <span>{meta?.updated_at ? `Directory updated ${new Date(meta.updated_at).toLocaleDateString()}` : null}</span>
          </div>
        ) : null}
      </div>

      {/* results */}
      {res && res.items.length === 0 ? (
        <p className="mx-auto mt-10 max-w-xl text-center text-sm text-gray-500">
          No exhibitors matched that search. Try a broader product word, or clear the fair/country filters.
        </p>
      ) : null}

      <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
        {(res?.items || []).map((it) => {
          const rv = revealed[it.id];
          return (
            <div
              key={it.id}
              className="flex flex-col rounded-2xl border border-gray-200 bg-white p-5 shadow-[0_1px_3px_rgba(0,0,0,0.04)]"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-base font-bold text-gray-900">{it.name}</h3>
                  <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500">
                    {it.fair ? (
                      <span className="inline-flex items-center gap-1">
                        <Building2 className="h-3.5 w-3.5" />
                        {it.fair}
                      </span>
                    ) : null}
                    {it.city || it.country ? (
                      <span className="inline-flex items-center gap-1">
                        <MapPin className="h-3.5 w-3.5" />
                        {[it.city, it.country].filter(Boolean).join(', ')}
                      </span>
                    ) : null}
                    {it.hall || it.booth ? (
                      <span>Hall {it.hall || '—'} · Booth {it.booth || '—'}</span>
                    ) : null}
                  </p>
                </div>
                <span className="shrink-0 rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-gray-500">
                  {it.category || 'Exhibitor'}
                </span>
              </div>

              {it.product_summary ? (
                <p className="mt-3 line-clamp-2 text-sm text-gray-600">{it.product_summary}</p>
              ) : null}

              {it.products.length > 0 ? (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {it.products.map((p) => (
                    <span key={p.name} className="rounded-full bg-primary-50 px-2 py-0.5 text-[11px] font-medium text-primary-700">
                      {p.name}
                    </span>
                  ))}
                </div>
              ) : null}

              {/* locked / revealed contacts */}
              <div className="mt-4 border-t border-gray-100 pt-3">
                {rv ? (
                  <div className="space-y-2">
                    <p className="text-[11px] font-medium uppercase tracking-wide text-gray-400">
                      Contact details · sourced
                    </p>
                    {rv.company.website ? (
                      <a
                        href={rv.company.website}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="flex items-center gap-1.5 text-sm font-medium text-primary-700 hover:underline"
                      >
                        <Globe className="h-3.5 w-3.5" />
                        {rv.company.website.replace(/^https?:\/\//, '')}
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    ) : null}
                    {rv.contacts.map((c) => (
                      <div key={c.kind + c.value} className="flex items-center justify-between gap-2 text-sm">
                        <span className="flex items-center gap-1.5 text-gray-800">
                          {c.kind === 'EMAIL' ? <Mail className="h-3.5 w-3.5 text-gray-400" /> : <MessageCircle className="h-3.5 w-3.5 text-gray-400" />}
                          {c.value}
                        </span>
                        {c.source_url ? (
                          <a
                            href={c.source_url}
                            target="_blank"
                            rel="noreferrer noopener"
                            className="shrink-0 text-[11px] text-gray-400 hover:text-gray-600"
                          >
                            source
                          </a>
                        ) : null}
                      </div>
                    ))}
                    {rv.contacts.length === 0 && !rv.company.website ? (
                      <p className="text-sm text-gray-500">No contact details published on the source page.</p>
                    ) : null}
                  </div>
                ) : (
                  <div className="flex items-center justify-between gap-3">
                    <span className="flex flex-wrap items-center gap-3 text-xs text-gray-500">
                      <span className={`inline-flex items-center gap-1 ${it.has_email ? 'text-gray-700' : ''}`}>
                        <Mail className="h-3.5 w-3.5" />
                        {it.has_email ? 'Email on file' : 'No email'}
                      </span>
                      <span className={`inline-flex items-center gap-1 ${it.has_phone ? 'text-gray-700' : ''}`}>
                        <MessageCircle className="h-3.5 w-3.5" />
                        {it.has_phone ? 'Phone on file' : 'No phone'}
                      </span>
                      <span className="inline-flex items-center gap-1 text-gray-400">
                        <Lock className="h-3.5 w-3.5" />
                        Locked
                      </span>
                    </span>
                    <button
                      onClick={() => void reveal(it.id)}
                      disabled={revealBusy === it.id}
                      className="shrink-0 rounded-full bg-gray-900 px-4 py-1.5 text-xs font-semibold text-white hover:bg-gray-800 disabled:opacity-60"
                    >
                      {revealBusy === it.id ? 'Revealing…' : 'Reveal contact'}
                    </button>
                  </div>
                )}
              </div>

              {/* source attribution */}
              {it.source_url ? (
                <a
                  href={it.source_url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="mt-3 inline-flex items-center gap-1 text-[11px] text-gray-400 hover:text-gray-600"
                >
                  <ExternalLink className="h-3 w-3" />
                  Fair directory listing
                </a>
              ) : null}
            </div>
          );
        })}
      </div>

      {/* pagination */}
      {res && pages > 1 ? (
        <div className="mt-6 flex items-center justify-center gap-4">
          <button
            onClick={() => void run({ page: page - 1 })}
            disabled={page <= 1 || busy}
            className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40"
          >
            ← Previous
          </button>
          <span className="text-sm text-gray-500">Page {page} of {pages}</span>
          <button
            onClick={() => void run({ page: page + 1 })}
            disabled={page >= pages || busy}
            className="rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40"
          >
            Next →
          </button>
        </div>
      ) : null}

      {/* honest-data footnotes */}
      <div className="mx-auto mt-10 max-w-3xl space-y-2 text-center text-xs leading-relaxed text-gray-400">
        <p>{res?.disclaimer || meta?.disclaimer}</p>
        <p>
          Directory search is free for a limited time ({meta?.free_per_day ?? '—'} searches per day per visitor).
          Websites and contact details are locked until you create a free account — we never guess or fabricate them.
        </p>
        <p>
          A listing means the company appeared in that fair&apos;s exhibitor directory — it is not a verified buyer
          or a guarantee of current trading activity. Verify before outreach.
        </p>
      </div>
    </section>
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
        Launch offer: directory search is free for a limited time. Founding members who sign up now keep
        €0 Starter pricing when paid tiers go live. Prices exclude VAT where applicable.
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
          <a href="#directory" className="hover:text-gray-900">Directory</a>
          <a href="#features" className="hover:text-gray-900">Features</a>
          <a href="#packages" className="hover:text-gray-900">Packages</a>
          <a href="#about" className="hover:text-gray-900">About</a>
          <Link to="/login" className="hover:text-gray-900">Sign in</Link>
        </nav>
        <p className="text-xs text-gray-400">© {new Date().getFullYear()} LeadPilot. All rights reserved.</p>
      </div>
      <p className="mx-auto max-w-4xl px-4 pb-6 text-center text-[11px] leading-relaxed text-gray-400">
        Exhibitor data is crawled from public trade-fair directories and attributed to its source.
        A listing indicates presence at that fair — not current buying/selling activity and not verified
        contactability. Directory search is free for a limited time; contact reveal requires a free account.
      </p>
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
        <DirectoryTool />
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
