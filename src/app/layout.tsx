import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import { Toaster } from 'sonner';
import './globals.css';

const inter = Inter({ subsets: ['latin'] });

export const metadata: Metadata = {
  title: 'MealieShopper',
  description: 'Albert Heijn recepten importeren in Mealie',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="nl">
      <body className={inter.className}>
        {children}
        <Toaster richColors position="top-right" />
      </body>
    </html>
  );
}
