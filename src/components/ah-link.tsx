'use client';

import { useState, useEffect } from 'react';
import { Copy, Check, AlertCircle, LogIn } from 'lucide-react';
import { Button } from '@/components/ui/button';

const DEVTOOLS_SCRIPT = `(async function() {
  const check = (obj) => {
    if (!obj || typeof obj !== 'object') return null;
    return obj.refreshToken || obj.refresh_token || obj.RefreshToken || null;
  };
  for (const k of Object.keys(localStorage)) {
    try { const rt = check(JSON.parse(localStorage.getItem(k))); if (rt) { console.log('✅ refreshToken:', rt); return; } } catch {}
  }
  for (const k of Object.keys(sessionStorage)) {
    try { const rt = check(JSON.parse(sessionStorage.getItem(k))); if (rt) { console.log('✅ refreshToken:', rt); return; } } catch {}
  }
  try {
    for (const {name} of await indexedDB.databases()) {
      const db = await new Promise((res,rej) => { const r=indexedDB.open(name); r.onsuccess=()=>res(r.result); r.onerror=rej; });
      for (const store of db.objectStoreNames) {
        const all = await new Promise(res => { const tx=db.transaction(store,'readonly'); const r=tx.objectStore(store).getAll(); r.onsuccess=()=>res(r.result); });
        for (const item of all) { const rt = check(item); if (rt) { console.log('✅ refreshToken (idb):', rt); return; } }
      }
    }
  } catch(e) {}
  console.log('❌ Niet gevonden. Gebruik de handmatige methode.');
})()`;

export function AhLink() {
  const [token, setToken] = useState('');
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [oauthResult, setOauthResult] = useState<{ type: 'error' | 'token'; value: string } | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const ahRefresh = params.get('ah_refresh');
    const ahError = params.get('ah_error');
    if (ahRefresh) {
      setOauthResult({ type: 'token', value: ahRefresh });
      setToken(ahRefresh);
      window.history.replaceState({}, '', window.location.pathname);
    } else if (ahError) {
      setOauthResult({ type: 'error', value: ahError });
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  async function save() {
    if (!token.trim()) return;
    setLoading(true);
    setError(null);

    const isShortCode = token.trim().length < 100 && !token.trim().startsWith('eyJ');
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
    <div className="max-w-2xl space-y-5">
      {/* Method 1: OAuth redirect */}
      <div className="rounded-xl border border-blue-200 bg-blue-50 p-5">
        <h2 className="text-sm font-semibold text-blue-900">Methode 1 — Inloggen via AH (aanbevolen)</h2>
        <p className="mt-1 text-xs text-blue-700">
          Klik op de knop, log in met je Passkey en de app vangt de code automatisch op.
          Als AH de redirect afwijst, zie je hieronder een foutmelding en gebruik je methode 2.
        </p>
        <Button className="mt-3" onClick={() => { window.location.href = '/api/ah/auth/start'; }}>
          <LogIn className="h-4 w-4" />
          Inloggen bij Albert Heijn
        </Button>

        {oauthResult?.type === 'error' && (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            AH redirect mislukt: {oauthResult.value}. Gebruik methode 2.
          </div>
        )}
        {oauthResult?.type === 'token' && (
          <div className="mt-3 rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-xs text-green-700">
            ✅ Refresh token ontvangen! Zie stap 2 hieronder.
          </div>
        )}
      </div>

      {/* Method 2: Manual */}
      <div className="rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-gray-900">Methode 2 — Handmatig via DevTools</h2>

        <ol className="mt-3 space-y-4 text-sm text-gray-700">
          <li className="flex gap-3">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-gray-700 text-[10px] font-bold text-white">a</span>
            <div className="flex-1">
              <p>Log in op <a href="https://www.ah.nl" target="_blank" className="text-blue-600 underline">www.ah.nl</a>, open DevTools (<kbd className="rounded border px-1 text-xs">F12</kbd>) → <strong>Console</strong> en run:</p>
              <div className="mt-1.5 flex items-start gap-2 rounded border border-gray-200 bg-gray-50 p-2 font-mono text-[10px] text-gray-600">
                <span className="flex-1 break-all">{DEVTOOLS_SCRIPT}</span>
                <button onClick={() => navigator.clipboard.writeText(DEVTOOLS_SCRIPT)} className="shrink-0 text-gray-400 hover:text-gray-700">
                  <Copy className="h-3 w-3" />
                </button>
              </div>
            </div>
          </li>
          <li className="flex gap-3">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-gray-700 text-[10px] font-bold text-white">b</span>
            <div className="flex-1">
              <p>Niets gevonden? Ga naar <strong>Network</strong> → herlaad de pagina → filter op <code className="bg-gray-100 px-0.5 rounded text-xs">ah.nl</code> → zoek een request met <code className="bg-gray-100 px-0.5 rounded text-xs">Authorization: Bearer ...</code> in de headers en kopieer de token.</p>
            </div>
          </li>
        </ol>
      </div>

      {/* Token input */}
      <div className="rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-gray-900">Token opslaan</h2>
        <p className="mt-1 mb-3 text-xs text-gray-500">Plak hier het refresh token (of access token) dat je hebt gevonden:</p>
        <textarea
          value={token}
          onChange={(e) => { setToken(e.target.value); setSaved(false); }}
          placeholder="Plak hier het token..."
          rows={3}
          className="w-full rounded-md border border-gray-300 px-3 py-2 text-xs font-mono focus:border-blue-500 focus:outline-none"
        />
        <Button size="sm" className="mt-2" onClick={save} disabled={!token.trim() || loading}>
          {loading ? 'Controleren...' : 'Controleren & opslaan'}
        </Button>

        {error && (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </div>
        )}

        {saved && (
          <div className="mt-4">
            <p className="mb-2 text-sm text-gray-700">Voeg dit toe aan <code className="rounded bg-gray-100 px-1 text-xs">.env.local</code> en herstart de server:</p>
            <div className="flex items-center gap-2 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 font-mono text-xs text-gray-800">
              <span className="flex-1 break-all">AH_REFRESH_TOKEN={token.trim()}</span>
              <button onClick={copyEnvLine} className="shrink-0 text-gray-500 hover:text-gray-900">
                {copied ? <Check className="h-4 w-4 text-green-600" /> : <Copy className="h-4 w-4" />}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
