'use client';

import * as Tabs from '@radix-ui/react-tabs';
import { ChefHat } from 'lucide-react';
import { RecipeSearch } from '@/components/recipe-search';
import { UrlImport } from '@/components/url-import';
import { WeekPlanner } from '@/components/week-planner';
import { cn } from '@/lib/utils';

export default function HomePage() {
  return (
    <div className="min-h-screen bg-gray-50">
      <header className="sticky top-0 z-10 border-b border-gray-200 bg-white">
        <div className="mx-auto flex h-16 max-w-6xl items-center gap-3 px-4">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-600">
            <ChefHat className="h-5 w-5 text-white" />
          </div>
          <div>
            <h1 className="text-base font-bold leading-none text-gray-900">MealieShopper</h1>
            <p className="mt-0.5 text-xs leading-none text-gray-500">Albert Heijn × Mealie</p>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8">
        <Tabs.Root defaultValue="planner">
          <Tabs.List className="mb-6 flex w-fit gap-1 rounded-lg border border-gray-200 bg-white p-1 shadow-sm">
            {(['planner', 'search', 'url'] as const).map((value) => (
              <Tabs.Trigger
                key={value}
                value={value}
                className={cn(
                  'rounded-md px-4 py-1.5 text-sm font-medium text-gray-600 transition-colors',
                  'hover:text-gray-900',
                  'data-[state=active]:bg-blue-600 data-[state=active]:text-white'
                )}
              >
                {value === 'planner' ? 'Weekmenu → AH' : value === 'search' ? 'Recepten zoeken' : 'URL importeren'}
              </Tabs.Trigger>
            ))}
          </Tabs.List>

          <Tabs.Content value="planner">
            <WeekPlanner />
          </Tabs.Content>

          <Tabs.Content value="search">
            <RecipeSearch />
          </Tabs.Content>

          <Tabs.Content value="url">
            <UrlImport />
          </Tabs.Content>
        </Tabs.Root>
      </main>
    </div>
  );
}
