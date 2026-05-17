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

  // Search for each ingredient in the AH product catalogue
  const searchResults = await Promise.allSettled(
    items.map(async (item) => {
      const product = await searchProduct(item.query);
      return { query: item.query, product, quantity: item.quantity };
    })
  );

  const matched: MatchedItem[] = [];
  const skipped: string[] = [];

  for (const result of searchResults) {
    if (result.status === 'fulfilled' && result.value.product) {
      matched.push(result.value as MatchedItem);
    } else {
      const query =
        result.status === 'fulfilled' ? result.value.query : 'onbekend';
      skipped.push(query);
    }
  }

  if (matched.length === 0) {
    return NextResponse.json(
      { error: 'Geen producten gevonden voor de opgegeven ingrediënten' },
      { status: 404 }
    );
  }

  // Add matched products to the AH shopping list
  await addToShoppingList(
    matched.map((m) => ({ productId: m.product.webshopId, quantity: m.quantity }))
  );

  return NextResponse.json({
    added: matched.length,
    skipped: skipped.length,
    skippedItems: skipped,
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
