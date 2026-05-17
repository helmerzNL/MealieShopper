import { NextRequest, NextResponse } from 'next/server';
import { searchRecipes } from '@/lib/ah';

export async function GET(req: NextRequest) {
  const q = req.nextUrl.searchParams.get('q');
  const page = Number(req.nextUrl.searchParams.get('page') ?? '0');

  if (!q?.trim()) {
    return NextResponse.json({ error: 'Zoekterm ontbreekt' }, { status: 400 });
  }

  try {
    const result = await searchRecipes(q.trim(), page);
    return NextResponse.json(result);
  } catch (err) {
    console.error('AH search error:', err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : 'Zoeken mislukt' },
      { status: 502 }
    );
  }
}
