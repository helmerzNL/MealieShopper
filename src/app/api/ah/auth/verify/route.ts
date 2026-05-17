import { NextRequest, NextResponse } from 'next/server';

const AH_API_BASE = 'https://api.ah.nl';

export async function POST(req: NextRequest) {
  const body = await req.json() as { refreshToken?: string };
  const token = body.refreshToken?.trim();
  if (!token) {
    return NextResponse.json({ error: 'Geen token opgegeven' }, { status: 400 });
  }

  // Try as refresh token first
  const res = await fetch(`${AH_API_BASE}/mobile-auth/v1/auth/token/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clientId: 'appie', refreshToken: token }),
  });

  if (res.ok) {
    return NextResponse.json({ ok: true, type: 'refresh' });
  }

  // Try as access token by calling a lightweight authenticated endpoint
  const checkRes = await fetch(`${AH_API_BASE}/mobile-services/member/v2/profile`, {
    headers: { Authorization: `Bearer ${token}` },
  });

  if (checkRes.ok) {
    return NextResponse.json({ ok: true, type: 'access' });
  }

  const text = await res.text().catch(() => '');
  return NextResponse.json(
    { error: `Token werkt niet (${res.status}): ${text.slice(0, 200)}` },
    { status: 400 }
  );
}
