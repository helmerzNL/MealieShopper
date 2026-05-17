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

// ── Product search (via AH GraphQL — no X-Application header required) ──────

export interface AhProduct {
  webshopId: string;
  title: string;
  price: { now: number; unitSize?: string };
  images: Array<{ url: string }>;
  brand: string | null;
  unitSize: string | null;
}

const PRODUCT_SEARCH_QUERY = `
  query Search($input: SearchProductsInput!) {
    searchProductsExperimental(input: $input) {
      products {
        id
        title
        brand
        salesUnitSize
        priceV2 { now }
        images { url }
      }
      totalFound
    }
  }
`;

export async function searchProduct(query: string): Promise<AhProduct | null> {
  const token = await getToken();

  const res = await fetch(`${AH_API_BASE}/graphql`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ query: PRODUCT_SEARCH_QUERY, variables: { input: { query, size: 3 } } }),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => '');
    console.error(`AH GraphQL product search mislukt (${res.status}) voor "${query}": ${body.slice(0, 200)}`);
    throw new Error(`AH productzoekopdracht mislukt (${res.status})`);
  }

  const data = await res.json();
  if (data.errors?.length) {
    console.error(`AH GraphQL errors voor "${query}":`, JSON.stringify(data.errors).slice(0, 200));
    return null;
  }

  const products = data.data?.searchProductsExperimental?.products ?? [];
  console.log(`AH GraphQL product search "${query}": ${products.length} resultaten`);

  const first = products[0];
  if (!first) return null;

  return {
    webshopId: String(first.id),
    title: first.title ?? query,
    price: { now: first.priceV2?.now ?? 0, unitSize: first.salesUnitSize ?? undefined },
    images: first.images ?? [],
    brand: first.brand ?? null,
    unitSize: first.salesUnitSize ?? null,
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
