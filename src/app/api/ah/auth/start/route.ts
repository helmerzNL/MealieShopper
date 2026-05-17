import { NextRequest, NextResponse } from 'next/server';

export async function GET(req: NextRequest) {
  const callbackUrl = new URL('/api/ah/auth/callback', req.url).toString();
  const params = new URLSearchParams({
    client_id: 'appie',
    redirect_uri: callbackUrl,
    response_type: 'code',
  });
  return NextResponse.redirect(
    `https://login.ah.nl/secure/oauth/authorize?${params}`,
    { status: 302 }
  );
}
