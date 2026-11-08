import { useEffect, useState } from 'react';
import { AlertTriangle, Database, Info } from 'lucide-react';
import { tradeCountries } from '../../lib/api';

// Shared helpers for the Global Trade tabs (Competitors / HS Intel / Compare).

export const errText = (e: unknown) =>
  (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
  (e as Error)?.message || 'Request failed';

export const fmtMoney = (v?: number | null) =>
  v == null ? '—' : new Intl.NumberFormat('en-US', {
    notation: 'compact', maximumFractionDigits: 1,
  }).format(v) + ' USD';

export const fmtFull = (v?: number | null) =>
  v == null ? '—' : new Intl.NumberFormat('en-US').format(v);

export interface Country { code: number; name: string; iso2?: string }

/** Reporter country list (official UN reference — loaded once per tab). */
export function useCountries(): Country[] {
  const [countries, setCountries] = useState<Country[]>([]);
  useEffect(() => {
    tradeCountries()
      .then((d) => setCountries(d.reporters || []))
      .catch(() => {});
  }, []);
  return countries;
}

interface SourceBlock {
  name?: string; url?: string; coverage?: string;
  disclaimer?: string; periods?: string[];
  destination_name?: string;
}

/**
 * Attribution banner: every trade payload shows its source, record type
 * (aggregated statistics — never shipments) and provider disclaimer.
 */
export function SourceBanner({ source, extra, note }: {
  source?: SourceBlock;
  extra?: React.ReactNode;
  note?: string;
}) {
  if (!source) return null;
  return (
    <div className="flex flex-wrap items-start gap-x-6 gap-y-2 rounded-2xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
      <span className="flex items-center gap-1.5 font-semibold">
        <Database className="h-4 w-4" /> Source: {source.name || 'UN Comtrade'}
      </span>
      <span>Record type: <b>aggregated statistics</b> (not shipments)</span>
      {source.destination_name && <span>Market: <b>{source.destination_name}</b></span>}
      {source.coverage && <span>Coverage: {source.coverage}</span>}
      {!!source.periods?.length && <span>Periods: {source.periods.join(', ')}</span>}
      {extra}
      {note && (
        <span className="flex items-start gap-1 basis-full text-xs text-blue-700">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {note}
        </span>
      )}
      {source.disclaimer && (
        <span className="flex items-start gap-1 basis-full text-xs text-blue-700">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {source.disclaimer}
        </span>
      )}
    </div>
  );
}

/** Error / stale-data banner shared by the tabs. */
export function Notice({ kind, children }: { kind: 'error' | 'stale'; children: React.ReactNode }) {
  return (
    <div className={`flex items-start gap-2 rounded-xl px-4 py-2 text-sm ${
      kind === 'error' ? 'bg-red-50 text-red-700' : 'bg-amber-50 text-amber-700'}`}>
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> <span>{children}</span>
    </div>
  );
}
