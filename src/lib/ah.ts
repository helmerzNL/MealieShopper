const AH_API_BASE = 'https://api.ah.nl';
const AH_CLIENT_ID = process.env.AH_CLIENT_ID ?? 'appie-android';

const AH_APP_HEADERS = {
  'X-Application': 'AHWEBSHOP',
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

// ── Product search (via AH webshop API — no application context required) ──

export interface AhProduct {
  webshopId: string;
  title: string;
  price: { now: number; unitSize?: string };
  images: Array<{ url: string }>;
  brand: string | null;
  unitSize: string | null;
}

interface WebshopProduct {
  webshopId: number | string;
  title?: string;
  brand?: string | null;
  unitSize?: string | null;
  price?: { now?: number; unitSize?: string };
  images?: Array<{ url: string }>;
  imageSrc?: string;
}

export async function searchProduct(query: string): Promise<AhProduct | null> {
  console.log('[ah.ts v2] searchProduct via webshop API voor:', query);
  const params = new URLSearchParams({ query, page: '0', pageSize: '3', sortBy: 'RELEVANCE' });
  const res = await fetch(`https://www.ah.nl/zoeken/api/products/search?${params}`, {
    headers: {
      Accept: 'application/json, text/plain, */*',
      'User-Agent': 'Mozilla/5.0 (compatible)',
    },
  });

  if (!res.ok) {
    console.error(`AH webshop product search mislukt (${res.status}) voor "${query}"`);
    throw new Error(`AH productzoekopdracht mislukt (${res.status})`);
  }

  const data = await res.json();
  const raw: WebshopProduct[] = data.products ?? data.cards?.flatMap((c: { products?: WebshopProduct[] }) => c.products ?? []) ?? [];
  console.log(`AH webshop product search "${query}": ${raw.length} resultaten`);

  const first = raw[0];
  if (!first) return null;

  return {
    webshopId: String(first.webshopId),
    title: first.title ?? query,
    price: { now: first.price?.now ?? 0, unitSize: first.price?.unitSize ?? first.unitSize ?? undefined },
    images: first.images ?? (first.imageSrc ? [{ url: first.imageSrc }] : []),
    brand: first.brand ?? null,
    unitSize: first.unitSize ?? first.price?.unitSize ?? null,
  };
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
