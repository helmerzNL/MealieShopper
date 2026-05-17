const AH_API_BASE = 'https://api.ah.nl';
const AH_CLIENT_ID = process.env.AH_CLIENT_ID ?? 'appie-android';

const AH_APP_HEADERS = {
  'X-Application': 'APPIE-ANDROID',
  'X-ClientName': 'appie-android',
};

interface TokenCache {
  token: string;
  expiresAt: number;
}

let anonTokenCache: TokenCache | null = null;
let userTokenCache: TokenCache | null = null;

// ── Anonymous token (for recipe search & product search) ───────────────────

async function getToken(): Promise<string> {
  if (anonTokenCache && Date.now() < anonTokenCache.expiresAt) {
    return anonTokenCache.token;
  }

  const res = await fetch(`${AH_API_BASE}/mobile-auth/v1/auth/token/anonymous`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AH_APP_HEADERS },
    body: JSON.stringify({ clientId: 'appie' }),
  });

  if (!res.ok) {
    throw new Error(`AH authenticatie mislukt (${res.status})`);
  }

  const data = await res.json() as { access_token: string; expires_in: number };
  anonTokenCache = {
    token: data.access_token,
    expiresAt: Date.now() + (data.expires_in - 60) * 1000,
  };
  return anonTokenCache.token;
}

// ── User token (for shopping list) ────────────────────────────────────────

export async function getUserToken(): Promise<string> {
  if (userTokenCache && Date.now() < userTokenCache.expiresAt) {
    return userTokenCache.token;
  }

  const username = process.env.AH_USERNAME;
  const password = process.env.AH_PASSWORD;
  if (!username || !password) {
    throw new Error('AH_USERNAME en AH_PASSWORD zijn niet ingevuld in .env.local.');
  }

  const res = await fetch(`${AH_API_BASE}/mobile-auth/v1/auth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AH_APP_HEADERS },
    body: JSON.stringify({ clientId: AH_CLIENT_ID, username, password }),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`AH inloggen mislukt (${res.status}): ${body}`);
  }

  const data = await res.json() as { access_token: string; expires_in: number };
  userTokenCache = {
    token: data.access_token,
    expiresAt: Date.now() + (data.expires_in - 60) * 1000,
  };
  return userTokenCache.token;
}

// ── Recipe search ──────────────────────────────────────────────────────────

export interface AhRecipeImage {
  url: string;
  width: number;
  height: number;
}

export interface AhRecipe {
  id: number;
  title: string;
  description?: string;
  images: AhRecipeImage[];
  cookTime?: number;
  preparationTime?: number;
  servings?: number;
  courses?: string[];
  keywords?: string[];
  webPath: string;
}

export interface AhSearchResponse {
  recipes: AhRecipe[];
  total: number;
}

export async function searchRecipes(query: string, page = 0, size = 12): Promise<AhSearchResponse> {
  const token = await getToken();
  const params = new URLSearchParams({
    searchTerms: query,
    page: String(page),
    size: String(size),
    sortBy: 'RELEVANCE',
  });

  const res = await fetch(`${AH_API_BASE}/mobile-services/recipe/search/v2?${params}`, {
    headers: { Authorization: `Bearer ${token}`, ...AH_APP_HEADERS },
  });

  if (!res.ok) {
    throw new Error(`AH zoeken mislukt (${res.status})`);
  }

  const data = await res.json();
  return {
    recipes: (data.result ?? data.recipes ?? []) as AhRecipe[],
    total: (data.totalFound ?? data.total ?? 0) as number,
  };
}

// ── Product search ─────────────────────────────────────────────────────────

export interface AhProduct {
  webshopId: string;
  title: string;
  price: { now: number; unitSize?: string };
  images: Array<{ url: string }>;
  brand: string | null;
  unitSize: string | null;
}

export async function searchProduct(query: string): Promise<AhProduct | null> {
  const token = await getToken();

  for (const endpoint of [
    `${AH_API_BASE}/mobile-services/product/search/v2`,
    `${AH_API_BASE}/mobile-services/product/search/v3`,
  ]) {
    const params = new URLSearchParams({ query, size: '3' });
    const res = await fetch(`${endpoint}?${params}`, {
      headers: { Authorization: `Bearer ${token}`, ...AH_APP_HEADERS },
    });

    if (res.status === 500) {
      const body = await res.text().catch(() => '');
      console.error(`AH product search 500 op ${endpoint} voor "${query}": ${body.slice(0, 300)}`);
      continue;
    }

    if (!res.ok) {
      console.error(`AH product search mislukt (${res.status}) op ${endpoint} voor "${query}"`);
      throw new Error(`AH productzoekopdracht mislukt (${res.status})`);
    }

    const data = await res.json();
    const products: AhProduct[] = data.products ?? data.result ?? [];
    console.log(`AH product search "${query}" via ${endpoint}: ${products.length} resultaten`);
    return products[0] ?? null;
  }

  throw new Error(`AH productzoekopdracht mislukt (500) voor "${query}"`);
}

// ── Shopping list ──────────────────────────────────────────────────────────

export interface ShoppingListItem {
  productId: string;
  quantity: number;
}

export async function addToShoppingList(items: ShoppingListItem[]): Promise<void> {
  const token = await getUserToken();

  const body = items.map(item => ({
    type: 'PRODUCT',
    productId: item.productId,
    quantity: item.quantity,
  }));

  // Try to add to existing shopping list
  const res = await fetch(`${AH_API_BASE}/mobile-services/shoppinglist/v2`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const errorBody = await res.text().catch(() => '');
    throw new Error(`AH winkelmandje vullen mislukt (${res.status}): ${errorBody}`);
  }
}
