import type { Metadata } from "next";
import Link from "next/link";

import "./globals.css";

export const metadata: Metadata = {
  title: "WC Predict — 2026 World Cup probabilities",
  description:
    "Elo→Poisson match predictions and Monte Carlo tournament simulation for the 2026 FIFA World Cup.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <header className="border-b border-pitch-700 bg-pitch-900/80 backdrop-blur">
          <nav className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
            <Link href="/" className="text-lg font-bold tracking-tight text-zinc-100">
              ⚽ WC<span className="text-accent">Predict</span>
              <span className="ml-2 text-xs font-normal text-zinc-500">2026</span>
            </Link>
            <div className="flex gap-5 text-sm text-zinc-400">
              <Link href="/" className="hover:text-accent">Matches</Link>
              <Link href="/tournament" className="hover:text-accent">Simulator</Link>
              <Link href="/models" className="hover:text-accent">Models</Link>
              <Link href="/value" className="hover:text-accent">Value</Link>
            </div>
          </nav>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-8">{children}</main>
        <footer className="mx-auto max-w-5xl px-4 pb-8 text-xs text-zinc-600">
          Model probabilities, not guarantees — no betting edge implies profit.{" "}
          <a className="underline" href="https://www.begambleaware.org" rel="noreferrer" target="_blank">
            Gamble responsibly
          </a>
          . Data: football-data.org · The Odds API · eloratings.net seeds.
        </footer>
      </body>
    </html>
  );
}
