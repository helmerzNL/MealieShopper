'use client';

import { useState } from 'react';
import { Calendar, ShoppingCart, Loader2, Check, ChevronLeft, ChevronRight, AlertCircle } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import type { MealPlanEntry, RecipeDetail, RecipeIngredient } from '@/lib/mealie';

interface MealPlanEntryWithRecipe extends MealPlanEntry {
  recipeDetail: RecipeDetail | null;
}

interface CartResultItem {
  query: string;
  quantity: number;
  product: { title: string; price: number; unitSize: string | null; image: string | null };
}

const MEAL_TYPE_LABEL: Record<string, string> = {
  breakfast: 'Ontbijt',
  lunch: 'Lunch',
  dinner: 'Diner',
  side: 'Bijgerecht',
};

const DAY_NAMES = ['zo', 'ma', 'di', 'wo', 'do', 'vr', 'za'];
const MONTH_NAMES = ['jan', 'feb', 'mrt', 'apr', 'mei', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'dec'];

function getWeekBounds(offset = 0) {
  const today = new Date();
  today.setDate(today.getDate() + offset * 7);
  const dow = today.getDay();
  const monday = new Date(today);
  monday.setDate(today.getDate() - (dow === 0 ? 6 : dow - 1));
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);

  const fmt = (d: Date) => d.toISOString().split('T')[0];
  const label = (d: Date) => `${d.getDate()} ${MONTH_NAMES[d.getMonth()]}`;
  return {
    start: fmt(monday),
    end: fmt(sunday),
    label: `${label(monday)} – ${label(sunday)}`,
  };
}

function formatDate(dateStr: string) {
  const d = new Date(dateStr + 'T00:00:00');
  return `${DAY_NAMES[d.getDay()]} ${d.getDate()} ${MONTH_NAMES[d.getMonth()]}`;
}

function aggregateIngredients(entries: MealPlanEntryWithRecipe[]): RecipeIngredient[] {
  const seen = new Set<string>();
  const result: RecipeIngredient[] = [];
  for (const entry of entries) {
    for (const ing of entry.recipeDetail?.recipeIngredient ?? []) {
      const key = ing.food?.name ?? ing.display;
      if (key && !seen.has(key.toLowerCase())) {
        seen.add(key.toLowerCase());
        result.push(ing);
      }
    }
  }
  return result;
}

export function WeekPlanner() {
  const [weekOffset, setWeekOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [entries, setEntries] = useState<MealPlanEntryWithRecipe[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [addingToCart, setAddingToCart] = useState(false);
  const [cartResult, setCartResult] = useState<CartResultItem[] | null>(null);

  const week = getWeekBounds(weekOffset);

  async function loadMealPlan() {
    setLoading(true);
    setError(null);
    setEntries(null);
    setCartResult(null);

    try {
      const res = await fetch(`/api/mealplan?start=${week.start}&end=${week.end}`);
      const data = await res.json() as MealPlanEntryWithRecipe[] | { error: string };
      if (!res.ok) throw new Error((data as { error: string }).error ?? 'Fout');
      setEntries(data as MealPlanEntryWithRecipe[]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Ophalen mislukt');
    } finally {
      setLoading(false);
    }
  }

  async function addToCart() {
    if (!entries) return;
    const ingredients = aggregateIngredients(entries);
    if (ingredients.length === 0) {
      toast.error('Geen ingrediënten gevonden in het weekmenu.');
      return;
    }

    setAddingToCart(true);
    setCartResult(null);

    try {
      const items = ingredients.map((ing) => ({
        query: ing.food?.name ?? ing.display,
        quantity: Math.max(1, Math.round(ing.quantity ?? 1)),
      }));

      const res = await fetch('/api/ah/cart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items }),
      });

      const data = await res.json() as {
        added: number;
        skipped: number;
        skippedItems: string[];
        items: CartResultItem[];
        error?: string;
      };

      if (!res.ok) throw new Error(data.error ?? 'Toevoegen mislukt');

      setCartResult(data.items);
      toast.success(
        `${data.added} producten toegevoegd aan je AH winkelmandje!` +
          (data.skipped > 0 ? ` (${data.skipped} niet gevonden)` : '')
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Toevoegen aan winkelmandje mislukt');
    } finally {
      setAddingToCart(false);
    }
  }

  const ingredients = entries ? aggregateIngredients(entries) : [];
  const byDay = entries
    ? entries.reduce<Record<string, MealPlanEntryWithRecipe[]>>((acc, e) => {
        (acc[e.date] ??= []).push(e);
        return acc;
      }, {})
    : {};
  const sortedDays = Object.keys(byDay).sort();

  return (
    <div className="space-y-6">
      {/* Week selector */}
      <div className="flex items-center gap-3">
        <Button variant="outline" size="icon" onClick={() => setWeekOffset((o) => o - 1)}>
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <div className="flex-1 text-center">
          <p className="text-sm font-medium text-gray-900">{week.label}</p>
          <p className="text-xs text-gray-500">{weekOffset === 0 ? 'Deze week' : weekOffset === 1 ? 'Volgende week' : weekOffset === -1 ? 'Vorige week' : `${weekOffset > 0 ? '+' : ''}${weekOffset} weken`}</p>
        </div>
        <Button variant="outline" size="icon" onClick={() => setWeekOffset((o) => o + 1)}>
          <ChevronRight className="h-4 w-4" />
        </Button>
        <Button onClick={loadMealPlan} disabled={loading} className="ml-2">
          {loading ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Laden...
            </>
          ) : (
            <>
              <Calendar className="h-4 w-4" />
              Weekmenu laden
            </>
          )}
        </Button>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {entries && entries.length === 0 && (
        <div className="py-12 text-center text-gray-500">
          Geen maaltijden gepland voor {week.label}.
        </div>
      )}

      {sortedDays.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {sortedDays.map((date) => (
            <div key={date} className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
                {formatDate(date)}
              </p>
              <div className="space-y-2">
                {byDay[date].map((entry) => (
                  <div key={entry.id}>
                    <span className="text-[10px] font-medium uppercase tracking-wide text-blue-500">
                      {MEAL_TYPE_LABEL[entry.entryType] ?? entry.entryType}
                    </span>
                    <p className="text-sm font-medium text-gray-900 leading-snug">
                      {entry.recipeDetail?.name ?? entry.recipe?.name ?? entry.title ?? '—'}
                    </p>
                    {entry.recipeDetail && (
                      <p className="text-xs text-gray-400">
                        {entry.recipeDetail.recipeIngredient.length} ingrediënten
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {ingredients.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-900">
              Alle ingrediënten ({ingredients.length})
            </h2>
            <Button onClick={addToCart} disabled={addingToCart}>
              {addingToCart ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Toevoegen...
                </>
              ) : (
                <>
                  <ShoppingCart className="h-4 w-4" />
                  Voeg toe aan AH winkelmandje
                </>
              )}
            </Button>
          </div>

          <ul className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
            {ingredients.map((ing, i) => (
              <li key={i} className="flex items-center gap-2 text-sm text-gray-700">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-blue-400" />
                {ing.display || ing.food?.name || '—'}
              </li>
            ))}
          </ul>
        </div>
      )}

      {cartResult && cartResult.length > 0 && (
        <div className="rounded-xl border border-green-200 bg-green-50 p-5">
          <div className="mb-3 flex items-center gap-2">
            <Check className="h-5 w-5 text-green-600" />
            <h2 className="text-sm font-semibold text-green-800">
              {cartResult.length} producten toegevoegd aan je winkelmandje
            </h2>
          </div>
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {cartResult.map((item, i) => (
              <li key={i} className="flex items-center gap-2 rounded-lg bg-white p-2 shadow-sm">
                {item.product.image && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={item.product.image} alt="" className="h-10 w-10 rounded object-contain" />
                )}
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium text-gray-900">{item.product.title}</p>
                  <p className="text-[10px] text-gray-500">
                    €{item.product.price.toFixed(2)}
                    {item.product.unitSize ? ` · ${item.product.unitSize}` : ''}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
