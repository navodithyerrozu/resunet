import { Instrument_Sans, JetBrains_Mono } from "next/font/google";

import "./globals.css";

import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

/**
 * Type pairing.
 *
 * Instrument Sans is a grotesque with slightly narrow, squared-off forms -- it
 * holds up at display sizes with tight tracking, which a neutral UI sans does
 * not, and it does not carry the editorial connotation a display serif would.
 * This is a tool, not a magazine.
 *
 * JetBrains Mono carries every measurement in the product: the metric half of a
 * bullet, ATS scores, attempt counts, timestamps. That is the one real
 * typographic argument here -- a metric is a measurement, and measurements
 * belong in tabular figures where digits line up down a column and a number
 * that changed is visible without reading it.
 *
 * Both are self-hosted at build time by next/font, so there is no render-blocking
 * request to Google and no layout shift. First build needs network access to
 * fetch them.
 */
const instrument = Instrument_Sans({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-instrument",
  weight: ["400", "500", "600", "700"],
});

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-mono-jb",
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: {
    default: "ResuNet",
    template: "%s · ResuNet",
  },
  description:
    "Tailor a resume to a posting, check what an ATS actually reads, and track every application in one place.",
  robots: {
    // Every route is behind a session. Indexing would only ever surface a login
    // page, and the app's URLs leak resume and application ids.
    index: false,
    follow: false,
  },
};

export const viewport: Viewport = {
  themeColor: "#f6f7f9",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${instrument.variable} ${jetbrains.variable}`}>
      <body className="min-h-screen bg-paper text-ink antialiased">{children}</body>
    </html>
  );
}
