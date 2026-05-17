const MEALIE_URL = (process.env.MEALIE_URL ?? '').replace(/\/$/, '');
const MEALIE_TOKEN = process.env.MEALIE_API_TOKEN ?? '';

function authHeaders() {
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${MEALIE_TOKEN}`,
  };
}

export async function importFromUrl(url: string): Promise<string> {
  if (!MEALIE_URL || !MEALIE_TOKEN) {
    throw new Error(
      'Mealie is niet geconfigureerd. Controleer MEALIE_URL en MEALIE_API_TOKEN in .env.local.'
    );
  }

  const res = await fetch(`${MEALIE_URL}/api/recipes/create-url`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ url, includeTags: true }),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`Mealie import mislukt (${res.status}): ${body}`);
  }

  const slug = await res.json() as string;
  return slug;
}

export function recipePageUrl(slug: string): string {
  return `${MEALIE_URL}/recipe/${slug}`;
}
