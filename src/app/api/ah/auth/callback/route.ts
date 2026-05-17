import { NextRequest, NextResponse } from 'next/server';

const AH_API_BASE = 'https://api.ah.nl';

export async function GET(req: NextRequest) {
  const code = req.nextUrl.searchParams.get('code');
  const error = req.nextUrl.searchParams.get('error');

  if (error) {
    const desc = req.nextUrl.searchParams.get('error_description') ?? error;
    return NextResponse.redirect(
      new URL(`/?ah_error=${encodeURIComponent(desc)}`, req.url)
    );
  }

  if (!code) {
    return NextResponse.redirect(new URL('/?ah_error=Geen+code+ontvangen', req.url));
  }

  const callbackUrl = new URL('/api/ah/auth/callback', req.url).toString();
  const res = await fetch(`${AH_API_BASE}/mobile-auth/v1/auth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clientId: 'appie', code, redirect_uri: callbackUrl }),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => '');
    return NextResponse.redirect(
      new URL(`/?ah_error=${encodeURIComponent(`Code inwisselen mislukt (${res.status}): ${body.slice(0, 100)}`)}`, req.url)
    );
  }

  const data = await res.json() as { access_token: string; refresh_token: string };
  return NextResponse.redirect(
    new URL(`/?ah_refresh=${encodeURIComponent(data.refresh_token)}`, req.url)
  );
}
