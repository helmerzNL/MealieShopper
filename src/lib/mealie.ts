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
    // Fallback: fetch the page ourselves and send the HTML to Mealie
    return await importViaHtml(url);
  }
}

export function recipePageUrl(slug: string): string {
  return `${MEALIE_URL}/recipe/${slug}`;
}
