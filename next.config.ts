import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: 'static.ah.nl' },
      { protocol: 'https', hostname: '*.ah.nl' },
    ],
  },
};

export default nextConfig;
