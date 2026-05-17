'use client';

import { useState } from 'react';
import Image from 'next/image';
import { Clock, Users, ChefHat, ExternalLink, Check, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import type { AhRecipe } from '@/lib/ah';

interface RecipeCardProps {
  recipe: AhRecipe;
}

export function RecipeCard({ recipe }: RecipeCardProps) {
  const [importing, setImporting] = useState(false);
  const [mealieUrl, setMealieUrl] = useState<string | null>(null);

  const ahUrl = `https://www.ah.nl${recipe.webPath}`;
  const imageUrl = recipe.images?.[0]?.url;
  const totalTime = (recipe.cookTime ?? 0) + (recipe.preparationTime ?? 0);

  async function handleImport() {
    setImporting(true);
    try {
      const res = await fetch('/api/mealie/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: ahUrl }),
      });
      const data = await res.json() as { slug?: string; mealieUrl?: string; error?: string };
      if (!res.ok) throw new Error(data.error ?? 'Import mislukt');

      setMealieUrl(data.mealieUrl ?? null);
      toast.success(`"${recipe.title}" geïmporteerd in Mealie!`, {
        action: data.mealieUrl
          ? { label: 'Bekijk recept', onClick: () => window.open(data.mealieUrl, '_blank') }
          : undefined,
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Import mislukt');
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="group relative flex flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm transition-shadow hover:shadow-md">
      <div className="relative aspect-[4/3] w-full overflow-hidden bg-gray-100">
        {imageUrl ? (
          <Image
            src={imageUrl}
            alt={recipe.title}
            fill
            className="object-cover transition-transform group-hover:scale-105"
            sizes="(max-width: 768px) 100vw, (max-width: 1200px) 50vw, 33vw"
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <ChefHat className="h-12 w-12 text-gray-300" />
          </div>
        )}
        <a
          href={ahUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-full bg-white/90 text-gray-600 opacity-0 shadow-sm transition-opacity group-hover:opacity-100 hover:text-blue-600"
        >
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </div>

      <div className="flex flex-1 flex-col p-4">
        <h3 className="mb-1 line-clamp-2 text-sm font-semibold leading-snug text-gray-900">
          {recipe.title}
        </h3>

        {recipe.description && (
          <p className="mb-2 line-clamp-2 text-xs text-gray-500">{recipe.description}</p>
        )}

        <div className="mt-auto flex items-center gap-3 pt-2 text-xs text-gray-500">
          {totalTime > 0 && (
            <span className="flex items-center gap-1">
              <Clock className="h-3.5 w-3.5" />
              {totalTime} min
            </span>
          )}
          {recipe.servings && (
            <span className="flex items-center gap-1">
              <Users className="h-3.5 w-3.5" />
              {recipe.servings} pers.
            </span>
          )}
        </div>

        {(recipe.courses?.length || recipe.keywords?.length) && (
          <div className="mt-2 flex flex-wrap gap-1">
            {[...(recipe.courses ?? []), ...(recipe.keywords ?? [])].slice(0, 3).map((tag) => (
              <span
                key={tag}
                className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-medium text-blue-700"
              >
                {tag}
              </span>
            ))}
          </div>
        )}

        <div className="mt-3">
          {mealieUrl ? (
            <Button
              variant="success"
              size="sm"
              className="w-full"
              onClick={() => window.open(mealieUrl, '_blank')}
            >
              <Check className="h-3.5 w-3.5" />
              Bekijk in Mealie
            </Button>
          ) : (
            <Button size="sm" className="w-full" onClick={handleImport} disabled={importing}>
              {importing ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Importeren...
                </>
              ) : (
                'Importeer in Mealie'
              )}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
