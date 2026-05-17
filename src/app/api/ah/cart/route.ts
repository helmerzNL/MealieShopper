import { NextRequest, NextResponse } from 'next/server';
import { searchProduct, addToShoppingList, type AhProduct } from '@/lib/ah';

interface CartRequestItem {
  query: string;
  quantity: number;
}

interface MatchedItem {
  query: string;
  product: AhProduct;
  quantity: number;
}

export async function POST(req: NextRequest) {
  const body = await req.json() as { items?: CartRequestItem[] };
  const items = body.items ?? [];

  if (items.length === 0) {
    return NextResponse.json({ error: 'Geen ingrediënten opgegeven' }, { status: 400 });
  }

  // Search sequentially to avoid rate limiting
  const searchResults: PromiseSettledResult<{ query: string; product: AhProduct | null; quantity: number }>[] = [];
  for (const item of items) {
    try {
      const product = await searchProduct(item.query);
      searchResults.push({ status: 'fulfilled', value: { query: item.query, product, quantity: item.quantity } });
    } catch (err) {
      searchResults.push({ status: 'rejected', reason: err });
    }
  }

  const matched: MatchedItem[] = [];
  const diagnostics: Array<{ query: string; status: string; error?: string }> = [];

  for (const result of searchResults) {
    if (result.status === 'fulfilled' && result.value.product) {
      matched.push(result.value as MatchedItem);
      diagnostics.push({ query: result.value.query, status: 'gevonden' });
    } else if (result.status === 'fulfilled') {
      diagnostics.push({ query: result.value.query, status: 'niet gevonden' });
    } else {
      diagnostics.push({ query: 'onbekend', status: 'fout', error: result.reason?.message });
    }
  }

  console.log('AH product search diagnostics:', JSON.stringify(diagnostics, null, 2));

  if (matched.length === 0) {
    return NextResponse.json(
      {
        error: 'Geen producten gevonden voor de opgegeven ingrediënten',
        diagnostics,
      },
      { status: 404 }
    );
  }

  // Add matched products to the AH shopping list
  await addToShoppingList(
    matched.map((m) => ({ productId: m.product.webshopId, quantity: m.quantity }))
  );

  return NextResponse.json({
    added: matched.length,
    skipped: diagnostics.filter((d) => d.status !== 'gevonden').length,
    skippedItems: diagnostics.filter((d) => d.status !== 'gevonden').map((d) => d.query),
    items: matched.map((m) => ({
      query: m.query,
      quantity: m.quantity,
      product: {
        title: m.product.title,
        price: m.product.price.now,
        unitSize: m.product.unitSize ?? m.product.price.unitSize,
        image: m.product.images?.[0]?.url ?? null,
      },
    })),
  });
}
