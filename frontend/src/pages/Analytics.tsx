import { useState, useEffect } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar,
} from 'recharts';
import { RefreshCw } from 'lucide-react';
import { overview, trends } from '../lib/api';

export default function Analytics() {
  const [data, setData] = useState<Record<string, number> | null>(null);
  const [daily, setDaily] = useState<{ date: string; discovered: number; sent: number }[]>([]);
  const [countries, setCountries] = useState<{ country: string; leads: number }[]>([]);
  const [days, setDays] = useState(30);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const load = async () => {
    const [o, t] = await Promise.all([overview(days), trends(days)]);
    setData(o);
    setDaily(t.daily);
    setCountries(t.by_country);
    setUpdatedAt(new Date());
  };
  useEffect(() => {
    load().catch(() => {});
  }, [days]);

  const coverage = [
    { name: 'With email', value: data?.with_email ?? 0 },
    { name: 'With phone', value: data?.with_phone ?? 0 },
    { name: 'WhatsApp identified', value: data?.whatsapp_identified ?? 0 },
  ];
  const outcomes = [
    { name: 'Attempted', value: data?.emails_attempted ?? 0 },
    { name: 'Delivered', value: data?.delivered ?? 0 },
    { name: 'Replies', value: data?.replies ?? 0 },
    { name: 'Bounced', value: data?.bounces ?? 0 },
    { name: 'Failed', value: data?.failed ?? 0 },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold text-gray-900">Analytics</h1>
        <div className="flex items-center gap-2">
          <select className="input w-32" value={days} onChange={(e) => setDays(parseInt(e.target.value))}>
            {[7, 30, 90].map((d) => <option key={d} value={d}>Last {d} days</option>)}
          </select>
          <button onClick={load} className="btn-secondary text-sm"><RefreshCw className="mr-1 h-4 w-4" /> Refresh</button>
        </div>
      </div>
      {updatedAt && <p className="text-xs text-gray-400">Last refreshed {updatedAt.toLocaleTimeString()} · all numbers are live database queries</p>}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card">
          <h2 className="font-semibold">Companies discovered & emails sent per day</h2>
          <div className="mt-3 h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={daily}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                <YAxis />
                <Tooltip />
                <Line type="monotone" dataKey="discovered" stroke="#2f6df6" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="sent" stroke="#10b981" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="card">
          <h2 className="font-semibold">Leads by country</h2>
          <div className="mt-3 h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={countries.slice(0, 10)} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis type="number" />
                <YAxis dataKey="country" type="category" width={110} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="leads" fill="#2f6df6" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="card">
          <h2 className="font-semibold">Contact coverage (companies)</h2>
          <div className="mt-3 h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={coverage}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                <YAxis />
                <Tooltip />
                <Bar dataKey="value" fill="#8b5cf6" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="card">
          <h2 className="font-semibold">Campaign outcomes (messages)</h2>
          <div className="mt-3 h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={outcomes}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                <YAxis />
                <Tooltip />
                <Bar dataKey="value" fill="#10b981" />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-2 text-xs text-gray-500">“Attempted” counts every send try; “Delivered” only counts provider-confirmed delivery. Accepted-by-provider is never shown as delivered.</p>
        </div>
      </div>
    </div>
  );
}
