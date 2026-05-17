const AH_API_BASE = 'https://api.ah.nl';
const AH_CLIENT_ID = process.env.AH_CLIENT_ID ?? 'appie-android';

interface TokenCache {
  token: string;
  expiresAt: number;
}

let tokenCache: TokenCache | null = null;

async function getToken(): Promise<string> {
  if (tokenCache && Date.now() < tokenCache.expiresAt) {
    return tokenCache.token;
  }

  const res = await fetch(`${AH_API_BASE}/mobile-auth/v1/auth/token/anonymous`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clientId: AH_CLIENT_ID }),
  });

  if (!res.ok) {
    throw new Error(`AH authenticatie mislukt (${res.status})`);
  }

  const data = await res.json() as { access_token: string; expires_in: number };
  tokenCache = {
    token: data.access_token,
    expiresAt: Date.now() + (data.expires_in - 60) * 1000,
  };

  return tokenCache.token;
}

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
    headers: { Authorization: `Bearer ${token}` },
  });

  if (!res.ok) {
    throw new Error(`AH zoeken mislukt (${res.status})`);
  }

  const data = await res.json();
  return {
    // The AH API uses 'result' or 'recipes' depending on version
    recipes: (data.result ?? data.recipes ?? []) as AhRecipe[],
    total: (data.totalFound ?? data.total ?? 0) as number,
  };
}
