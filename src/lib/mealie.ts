const MEALIE_URL = (process.env.MEALIE_URL ?? '').replace(/\/$/, '');
const MEALIE_TOKEN = process.env.MEALIE_API_TOKEN ?? '';

const BROWSER_HEADERS = {
  'User-Agent':
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
  'Accept-Language': 'nl-NL,nl;q=0.9,en;q=0.8',
};

function authHeaders() {
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${MEALIE_TOKEN}`,
  };
}

async function importViaUrl(url: string): Promise<string> {
  const res = await fetch(`${MEALIE_URL}/api/recipes/create/url`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ url, include_tags: true, include_categories: true }),
  });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json() as Promise<string>;
}

async function importViaHtml(url: string): Promise<string> {
  const pageRes = await fetch(url, { headers: BROWSER_HEADERS });
  if (!pageRes.ok) {
    throw new Error(`Kon de pagina niet ophalen (${pageRes.status})`);
  }
  const html = await pageRes.text();

  const res = await fetch(`${MEALIE_URL}/api/recipes/create/html-or-json`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ data: html, url, include_tags: true, include_categories: true }),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`Mealie import mislukt (${res.status}): ${body}`);
  }
  return res.json() as Promise<string>;
}

export async function importFromUrl(url: string): Promise<string> {
  if (!MEALIE_URL || !MEALIE_TOKEN) {
    throw new Error(
      'Mealie is niet geconfigureerd. Controleer MEALIE_URL en MEALIE_API_TOKEN in .env.local.'
    );
  }

  try {
    return await importViaUrl(url);
  } catch {
    return await importViaHtml(url);
  }
}

export function recipePageUrl(slug: string): string {
  return `${MEALIE_URL}/recipe/${slug}`;
}

// ── Meal plan ──────────────────────────────────────────────────────────────

export interface MealPlanEntry {
  id: number;
  date: string;
  entryType: 'breakfast' | 'lunch' | 'dinner' | 'side';
  title: string | null;
  recipeId: string | null;
  recipe: { id: string; slug: string; name: string } | null;
}

export interface RecipeIngredient {
  quantity: number | null;
  unit: { name: string; abbreviation: string } | null;
  food: { name: string } | null;
  note: string | null;
  display: string;
  title: string | null;
}

export interface RecipeDetail {
  id: string;
  slug: string;
  name: string;
  recipeYield: string | null;
  recipeIngredient: RecipeIngredient[];
}

export async function getMealPlan(startDate: string, endDate: string): Promise<MealPlanEntry[]> {
  if (!MEALIE_URL || !MEALIE_TOKEN) {
    throw new Error('Mealie is niet geconfigureerd.');
  }
  const params = new URLSearchParams({ start_date: startDate, end_date: endDate, perPage: '50' });
  const res = await fetch(`${MEALIE_URL}/api/households/mealplans?${params}`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`Weekmenu ophalen mislukt (${res.status}): ${body}`);
  }
  const data = await res.json() as { items?: MealPlanEntry[] };
  return data.items ?? [];
}

export async function getRecipe(slug: string): Promise<RecipeDetail> {
  const res = await fetch(`${MEALIE_URL}/api/recipes/${slug}`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    throw new Error(`Recept ophalen mislukt (${res.status})`);
  }
  return res.json() as Promise<RecipeDetail>;
}
