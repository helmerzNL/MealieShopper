import { NextRequest, NextResponse } from 'next/server';
import { exchangeOAuthCode } from '@/lib/ah';

export async function POST(req: NextRequest) {
  const body = await req.json() as { code?: string };
  if (!body.code) {
    return NextResponse.json({ error: 'Geen code opgegeven' }, { status: 400 });
  }

  try {
    const { refreshToken } = await exchangeOAuthCode(body.code.trim());
    return NextResponse.json({ refreshToken });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : 'Onbekende fout' },
      { status: 500 }
    );
  }
}
