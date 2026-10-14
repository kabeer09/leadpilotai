import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Building2, Mail, MessageSquare, Globe, AlertCircle, CheckCircle2 } from 'lucide-react';
import { overview, health } from '../lib/api';
import { useAuth } from '../auth';

export default function Dashboard() {
  const { user } = useAuth();
  const [data, setData] = useState<Record<string, number> | null>(null);
  const [sys, setSys] = useState<{ email_provider: string; provider_configured: boolean; warnings: string[] } | null>(null);

  useEffect(() => {
    overview().then(setData).catch(() => {});
    health().then(setSys).catch(() => {});
  }, []);

  const cards = [
    { l: 'Companies', v: data?.companies ?? 0, icon: Building2 },
    { l: 'With business email', v: data?.with_email ?? 0, icon: Mail },
    { l: 'With phone', v: data?.with_phone ?? 0, icon: Building2 },
    { l: 'WhatsApp identified', v: data?.whatsapp_identified ?? 0, icon: MessageSquare },
    { l: 'Qualified leads', v: data?.qualified_leads ?? 0, icon: CheckCircle2 },
    { l: 'Emails attempted', v: data?.emails_attempted ?? 0, icon: Mail },
    { l: 'Replies', v: data?.replies ?? 0, icon: MessageSquare },
    { l: 'Bounces + failures', v: (data?.bounces ?? 0) + (data?.failed ?? 0), icon: AlertCircle },
    { l: 'Follow-ups due', v: data?.followups_due ?? 0, icon: Globe },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Welcome{user?.name ? `, ${user.name}` : ''}</h1>
        <p className="mt-1 text-sm text-gray-500">Your workspace at a glance — every number below is a live database query.</p>
      </div>
      {!user?.email_verified && (
        <div className="flex items-center gap-2 rounded-lg bg-yellow-50 p-3 text-sm text-yellow-800">
          <AlertCircle className="h-4 w-4" />
          Verify your email (Settings) to unlock live email sending. Dry-run mode works meanwhile.
        </div>
      )}
      {sys && !sys.provider_configured && (
        <div className="flex items-center gap-2 rounded-lg bg-gray-100 p-3 text-sm text-gray-700">
          <AlertCircle className="h-4 w-4" />
          Email provider ({sys.email_provider}) isn't fully configured — campaigns run in {sys.email_provider === 'dryrun' ? 'dry-run mode (nothing leaves the server)' : 'a degraded state'}. See Settings.
        </div>
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        {cards.map((c) => (
          <div key={c.l} className="card flex items-center gap-3">
            <c.icon className="h-6 w-6 shrink-0 text-primary-600" />
            <div>
              <p className="text-2xl font-bold text-gray-900">{c.v.toLocaleString()}</p>
              <p className="text-xs text-gray-500">{c.l}</p>
            </div>
          </div>
        ))}
      </div>
      <div className="card">
        <h2 className="font-semibold text-gray-900">Next steps</h2>
        <div className="mt-2 flex flex-wrap gap-2 text-sm">
          <Link to="/crawling" className="btn-secondary text-sm">Crawl a website</Link>
          <Link to="/companies" className="btn-secondary text-sm">Browse companies</Link>
          <Link to="/campaigns" className="btn-secondary text-sm">Create a campaign</Link>
        </div>
      </div>
    </div>
  );
}
