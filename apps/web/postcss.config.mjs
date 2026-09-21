/**
 * Tailwind v4 does its configuration in CSS (`@theme` in globals.css), so there
 * is no JS config for colours, spacing, or fonts. This file exists only for the
 * PostCSS plugin chain.
 *
 * @type {import('postcss-load-config').Config}
 */
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
