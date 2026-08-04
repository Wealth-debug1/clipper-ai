"use client";

import Link from "next/link";

export default function Nav() {
  return (
    <nav className="flex items-center gap-6 border-b border-black/10 dark:border-white/10 px-6 py-4">
      <span className="font-semibold">Kids Studio</span>
      <Link href="/" className="text-sm hover:underline">Library</Link>
      <Link href="/settings" className="text-sm hover:underline">Settings</Link>
    </nav>
  );
}
