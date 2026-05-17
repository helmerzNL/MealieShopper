import { NextRequest, NextResponse } from 'next/server';

const AH_API_BASE = 'https://api.ah.nl';

export async function POST(req: NextRequest) {
  const body = await req.json() as { refreshToken?: string };
  if (!body.refreshToken) {
    return NextResponse.json({ error: 'Geen token opgegeven' }, { status: 400 });
  }

  const res = await fetch(`${AH_API_BASE}/mobile-auth/v1/auth/token/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clientId: 'appie', refreshToken: body.refreshToken }),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    return NextResponse.json({ error: `Token werkt niet (${res.status}): ${text.slice(0, 200)}` }, { status: 400 });
  }

  return NextResponse.json({ ok: true });
}
