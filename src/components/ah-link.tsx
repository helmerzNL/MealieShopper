'use client';

import { useState } from 'react';
import { Copy, Check, AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';

const DEVTOOLS_SCRIPT = `(function() {
  const keys = Object.keys(localStorage);
  for (const k of keys) {
    const v = localStorage.getItem(k) ?? '';
    try {
      const obj = JSON.parse(v);
      if (obj?.refreshToken) { console.log('refreshToken:', obj.refreshToken); return obj.refreshToken; }
      if (obj?.refresh_token) { console.log('refresh_token:', obj.refresh_token); return obj.refresh_token; }
    } catch {}
    if (k.toLowerCase().includes('refresh') && v.length > 20) {
      console.log(k + ':', v); return v;
    }
  }
  console.log('Niet gevonden in localStorage. Probeer de Network methode.');
})()`;

export function AhLink() {
  const [token, setToken] = useState('');
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Try treating input as either a refresh token or an OAuth code
  async function save() {
    if (!token.trim()) return;
    setLoading(true);
    setError(null);

    // If it looks like an OAuth code (short, alphanumeric) try exchanging it
    const isShortCode = token.trim().length < 100;
    if (isShortCode) {
      try {
        const res = await fetch('/api/ah/auth', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: token.trim() }),
        });
        const data = await res.json() as { refreshToken?: string; error?: string };
        if (res.ok && data.refreshToken) {
          setToken(data.refreshToken);
          setSaved(true);
          setLoading(false);
          return;
        }
      } catch {}
    }

    // Otherwise treat as refresh token and verify it works
    try {
      const res = await fetch('/api/ah/auth/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refreshToken: token.trim() }),
      });
      const data = await res.json() as { ok?: boolean; error?: string };
      if (!res.ok) throw new Error(data.error ?? 'Token werkt niet');
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Onbekende fout');
    } finally {
      setLoading(false);
    }
  }

  function copyEnvLine() {
    navigator.clipboard.writeText(`AH_REFRESH_TOKEN=${token.trim()}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="space-y-5 rounded-xl border border-gray-200 bg-white p-6 shadow-sm max-w-2xl">
      <div>
        <h2 className="text-base font-semibold text-gray-900">Albert Heijn account koppelen</h2>
        <p className="mt-1 text-sm text-gray-500">
          Haal je AH refresh token op via de browser DevTools terwijl je ingelogd bent op ah.nl.
        </p>
      </div>

      <ol className="space-y-5 text-sm text-gray-700">
        <li className="flex gap-3">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-blue-600 text-xs font-bold text-white">1</span>
          <div>
            <p>Ga naar <a href="https://www.ah.nl" target="_blank" className="text-blue-600 underline">www.ah.nl</a> en log in met je account.</p>
          </div>
        </li>

        <li className="flex gap-3">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-blue-600 text-xs font-bold text-white">2</span>
          <div className="flex-1">
            <p>Open DevTools (<kbd className="rounded border border-gray-300 bg-gray-100 px-1 text-xs">F12</kbd>), ga naar het tabblad <strong>Console</strong> en plak dit script:</p>
            <div className="mt-2 flex items-start gap-2 rounded-md border border-gray-200 bg-gray-50 p-3 font-mono text-xs text-gray-700">
              <span className="flex-1 whitespace-pre-wrap break-all">{DEVTOOLS_SCRIPT}</span>
              <button
                onClick={() => { navigator.clipboard.writeText(DEVTOOLS_SCRIPT); }}
                className="shrink-0 text-gray-400 hover:text-gray-700 mt-0.5"
                title="Kopieer script"
              >
                <Copy className="h-3.5 w-3.5" />
              </button>
            </div>
            <p className="mt-1 text-xs text-gray-500">Druk op Enter. Het refresh token verschijnt in de console output.</p>
          </div>
        </li>

        <li className="flex gap-3">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-blue-600 text-xs font-bold text-white">3</span>
          <div className="flex-1">
            <p className="mb-2">Plak het token hieronder:</p>
            <textarea
              value={token}
              onChange={(e) => { setToken(e.target.value); setSaved(false); }}
              placeholder="Plak hier het refresh token..."
              rows={3}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-xs font-mono focus:border-blue-500 focus:outline-none"
            />
            <Button size="sm" className="mt-2" onClick={save} disabled={!token.trim() || loading}>
              {loading ? 'Controleren...' : 'Controleren & opslaan'}
            </Button>
          </div>
        </li>

        {error && (
          <li className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </li>
        )}

        {saved && (
          <li className="flex gap-3">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-600 text-xs font-bold text-white">4</span>
            <div className="flex-1">
              <p className="mb-2">Voeg dit toe aan je <code className="rounded bg-gray-100 px-1 text-xs">.env.local</code> en herstart de server:</p>
              <div className="flex items-center gap-2 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 font-mono text-xs text-gray-800">
                <span className="flex-1 break-all">AH_REFRESH_TOKEN={token.trim()}</span>
                <button onClick={copyEnvLine} className="shrink-0 text-gray-500 hover:text-gray-900">
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
