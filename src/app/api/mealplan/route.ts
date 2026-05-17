import { NextRequest, NextResponse } from 'next/server';
import { getMealPlan, getRecipe, type MealPlanEntry, type RecipeDetail } from '@/lib/mealie';

export interface MealPlanEntryWithRecipe extends MealPlanEntry {
  recipeDetail: RecipeDetail | null;
}

export async function GET(req: NextRequest) {
  const start = req.nextUrl.searchParams.get('start');
  const end = req.nextUrl.searchParams.get('end');

  if (!start || !end) {
    return NextResponse.json({ error: 'start en end parameters zijn verplicht' }, { status: 400 });
  }

  try {
    const entries = await getMealPlan(start, end);

    const uniqueSlugs = [
      ...new Set(
        entries
          .filter((e): e is MealPlanEntry & { recipe: NonNullable<MealPlanEntry['recipe']> } =>
            e.recipe?.slug != null
          )
          .map((e) => e.recipe.slug)
      ),
    ];

    const recipeResults = await Promise.allSettled(uniqueSlugs.map((slug) => getRecipe(slug)));
    const recipeMap = new Map<string, RecipeDetail>();
    recipeResults.forEach((result, i) => {
      if (result.status === 'fulfilled') {
        recipeMap.set(uniqueSlugs[i], result.value);
      }
    });

    const result: MealPlanEntryWithRecipe[] = entries.map((entry) => ({
      ...entry,
      recipeDetail: entry.recipe?.slug ? (recipeMap.get(entry.recipe.slug) ?? null) : null,
    }));

    return NextResponse.json(result);
  } catch (err) {
    console.error('Mealplan error:', err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : 'Fout bij ophalen weekmenu' },
      { status: 502 }
    );
  }
}
