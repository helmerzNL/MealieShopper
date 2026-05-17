'use client';

import { useState } from 'react';
import { ExternalLink, Copy, Check, AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { AH_OAUTH_URL } from '@/lib/ah';

export function AhLink() {
  const [code, setCode] = useState('');
  const [refreshToken, setRefreshToken] = useState('');
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function exchange() {
    if (!code.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/ah/auth', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code.trim() }),
      });
      const data = await res.json() as { refreshToken?: string; error?: string };
      if (!res.ok) throw new Error(data.error ?? 'Fout');
      setRefreshToken(data.refreshToken ?? '');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Onbekende fout');
    } finally {
      setLoading(false);
    }
  }

  function copy() {
    navigator.clipboard.writeText(`AH_REFRESH_TOKEN=${refreshToken}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="space-y-5 rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
      <div>
        <h2 className="text-base font-semibold text-gray-900">Albert Heijn account koppelen</h2>
        <p className="mt-1 text-sm text-gray-500">
          AH gebruikt OAuth. Volg de stappen om je account eenmalig te koppelen.
        </p>
      </div>

      <ol className="space-y-4 text-sm text-gray-700">
        <li className="flex gap-3">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-blue-600 text-xs font-bold text-white">1</span>
          <div>
            <p>Klik op de knop hieronder om in te loggen bij AH. Na het inloggen probeert de browser te openen met <code className="rounded bg-gray-100 px-1 text-xs">appie://login-exit?code=...</code></p>
            <Button
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={() => window.open(AH_OAUTH_URL, '_blank')}
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Inloggen bij AH
            </Button>
          </div>
        </li>

        <li className="flex gap-3">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-blue-600 text-xs font-bold text-white">2</span>
          <div className="flex-1">
            <p>Kopieer de <code className="rounded bg-gray-100 px-1 text-xs">code=...</code> waarde uit de adresbalk of de foutmelding van de browser en plak hem hieronder:</p>
            <input
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="Plak hier de code..."
              className="mt-2 w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            />
            <Button size="sm" className="mt-2" onClick={exchange} disabled={!code.trim() || loading}>
              {loading ? 'Inwisselen...' : 'Inwisselen'}
            </Button>
          </div>
        </li>

        {error && (
          <li className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </li>
        )}

        {refreshToken && (
          <li className="flex gap-3">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-600 text-xs font-bold text-white">3</span>
            <div className="flex-1">
              <p>Voeg dit toe aan je <code className="rounded bg-gray-100 px-1 text-xs">.env.local</code> en herstart de server:</p>
              <div className="mt-2 flex items-center gap-2 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 font-mono text-xs text-gray-800">
                <span className="flex-1 break-all">AH_REFRESH_TOKEN={refreshToken}</span>
                <button onClick={copy} className="shrink-0 text-gray-500 hover:text-gray-900">
                  {copied ? <Check className="h-4 w-4 text-green-600" /> : <Copy className="h-4 w-4" />}
                </button>
              </div>
            </div>
          </li>
        )}
      </ol>
    </div>
  );
}
