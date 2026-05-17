'use client';

import { useState } from 'react';
import { Link, Loader2, Check, ExternalLink } from 'lucide-react';
import { toast } from 'sonner';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';

export function UrlImport() {
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [mealieUrl, setMealieUrl] = useState<string | null>(null);

  async function handleImport(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;

    setLoading(true);
    setMealieUrl(null);

    try {
      const res = await fetch('/api/mealie/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim() }),
      });
      const data = await res.json() as { mealieUrl?: string; error?: string };
      if (!res.ok) throw new Error(data.error ?? 'Import mislukt');

      setMealieUrl(data.mealieUrl ?? null);
      toast.success('Recept geïmporteerd in Mealie!');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Import mislukt');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-xl space-y-4">
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h2 className="mb-1 text-base font-semibold text-gray-900">Recept importeren via URL</h2>
        <p className="mb-4 text-sm text-gray-500">
          Plak een link van{' '}
          <a
            href="https://www.ah.nl/allerhande"
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 hover:underline"
          >
            Allerhande
          </a>{' '}
          of een andere receptensite.
        </p>

        <form onSubmit={handleImport} className="flex gap-2">
          <div className="relative flex-1">
            <Link className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.ah.nl/allerhande/recept/..."
              type="url"
              className="pl-9"
            />
          </div>
          <Button type="submit" disabled={loading || !url.trim()}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Importeer'}
          </Button>
        </form>
      </div>

      {mealieUrl && (
        <div className="flex items-center gap-3 rounded-xl border border-green-200 bg-green-50 px-4 py-3">
          <Check className="h-5 w-5 shrink-0 text-green-600" />
          <p className="flex-1 text-sm text-green-800">Recept succesvol geïmporteerd in Mealie!</p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => window.open(mealieUrl, '_blank')}
            className="shrink-0 border-green-300 text-green-700 hover:bg-green-100"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            Bekijken
          </Button>
        </div>
      )}

      <div className="rounded-xl border border-gray-100 bg-white p-4">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
          Ondersteunde sites
        </h3>
        <ul className="space-y-1 text-sm text-gray-600">
          <li>• allerhande.nl (Albert Heijn)</li>
          <li>• jumbo.com</li>
          <li>• leukerecepten.nl</li>
          <li>• En 300+ andere receptensites via Mealie</li>
        </ul>
      </div>
    </div>
  );
}
