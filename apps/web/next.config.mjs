/**
 * Next.js configuration.
 *
 * @type {import('next').NextConfig}
 */
const nextConfig = {
  reactStrictMode: true,

  // `@resunet/shared` ships TypeScript source rather than compiled JS, so Next
  // has to run it through its own compiler. Without this the build fails on the
  // first `import type` it cannot parse.
  transpilePackages: ["@resunet/shared"],

  eslint: {
    // Lint is a separate CI step. Folding it into `next build` means a style
    // violation blocks a deploy that would otherwise be fine, and it slows the
    // build for everyone.
    ignoreDuringBuilds: true,
  },

  typescript: {
    // Never true. A type error here means the frontend disagrees with the
    // generated API types, which is exactly the class of bug that
    // `packages/shared` exists to catch -- shipping past it defeats the point.
    ignoreBuildErrors: false,
  },

  // The API base URL is read server-side only (see src/lib/api/client.ts), so it
  // is deliberately NOT exposed as NEXT_PUBLIC_*. The browser talks to this
  // app's own route handlers, which hold the session cookie; it never holds a
  // bearer token or addresses FastAPI directly.
  env: {},

  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
          {
            // Resume PDFs render in an iframe from our own origin, and the
            // Matter.js canvas needs no external anything, so the policy can
            // stay tight. `unsafe-inline` for styles is Tailwind's runtime
            // injection in dev; tighten with a nonce if this ever ships
            // user-generated HTML.
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",
              "img-src 'self' data: blob:",
              "style-src 'self' 'unsafe-inline'",
              "script-src 'self' 'unsafe-eval' 'unsafe-inline'",
              "frame-src 'self' blob:",
              "object-src 'none'",
              "base-uri 'none'",
              "form-action 'self'",
            ].join("; "),
          },
        ],
      },
    ];
  },
};

export default nextConfig;
