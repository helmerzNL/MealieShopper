import { NextRequest, NextResponse } from 'next/server';
import { importFromUrl, recipePageUrl } from '@/lib/mealie';

export async function POST(req: NextRequest) {
  const body = await req.json() as { url?: string };
  const url = body.url?.trim();

  if (!url) {
    return NextResponse.json({ error: 'URL ontbreekt' }, { status: 400 });
  }

  try {
    const slug = await importFromUrl(url);
    return NextResponse.json({ slug, mealieUrl: recipePageUrl(slug) });
  } catch (err) {
    console.error('Mealie import error:', err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : 'Import mislukt' },
      { status: 502 }
    );
  }
}
