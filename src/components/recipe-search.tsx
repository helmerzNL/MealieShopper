'use client';

import { useState, useCallback } from 'react';
import { Search, Loader2 } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { RecipeCard } from '@/components/recipe-card';
import type { AhRecipe, AhSearchResponse } from '@/lib/ah';

export function RecipeSearch() {
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AhSearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);

  const search = useCallback(async (q: string, p = 0) => {
    if (!q.trim()) return;
    setLoading(true);
    setError(null);

    try {
      const res = await fetch(`/api/ah/search?q=${encodeURIComponent(q)}&page=${p}`);
      const data = await res.json() as AhSearchResponse & { error?: string };
      if (!res.ok) throw new Error(data.error ?? 'Zoeken mislukt');
      setResult(data);
      setPage(p);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Er is een fout opgetreden');
    } finally {
      setLoading(false);
    }
  }, []);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    search(query, 0);
  }

  const pageSize = 12;
  const hasNext = result ? result.total > (page + 1) * pageSize : false;

  return (
    <div className="space-y-6">
      <form onSubmit={handleSubmit} className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Zoek een recept op Allerhande..."
            className="pl-9"
          />
        </div>
        <Button type="submit" disabled={loading || !query.trim()}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Zoeken'}
        </Button>
      </form>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {result && (
        <>
          <p className="text-sm text-gray-500">
            {result.total.toLocaleString('nl-NL')} recepten gevonden
          </p>

          {result.recipes.length > 0 ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {result.recipes.map((recipe: AhRecipe) => (
                <RecipeCard key={recipe.id} recipe={recipe} />
              ))}
            </div>
          ) : (
            <p className="py-12 text-center text-gray-500">
              Geen recepten gevonden voor &quot;{query}&quot;
            </p>
          )}

          {(page > 0 || hasNext) && (
            <div className="flex justify-center gap-2 pt-2">
              {page > 0 && (
                <Button
                  variant="outline"
                  onClick={() => search(query, page - 1)}
                  disabled={loading}
                >
                  Vorige
                </Button>
              )}
              {hasNext && (
                <Button
                  variant="outline"
                  onClick={() => search(query, page + 1)}
                  disabled={loading}
                >
                  Volgende
                </Button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
