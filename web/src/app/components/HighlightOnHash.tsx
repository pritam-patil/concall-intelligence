"use client";

import { useEffect, useState, type ReactNode } from "react";

/**
 * Briefly tints its children when the page was opened at `#<hash>` — e.g.
 * arriving at the landing page's companies section from the chat header's
 * "Covered companies" link — so a visitor who just jumped here can see where
 * they landed. Done in React rather than CSS `:target` because Next's
 * client-side navigation sets the fragment via pushState, which `:target`
 * doesn't track. The animation itself is `.target-flash` in globals.css.
 */
export default function HighlightOnHash({
  hash,
  className = "",
  children,
}: {
  hash: string;
  className?: string;
  children: ReactNode;
}) {
  const [flash, setFlash] = useState(false);

  useEffect(() => {
    const check = () => {
      if (window.location.hash === `#${hash}`) setFlash(true);
    };
    check();
    window.addEventListener("hashchange", check);
    return () => window.removeEventListener("hashchange", check);
  }, [hash]);

  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => setFlash(false), 1600);
    return () => clearTimeout(t);
  }, [flash]);

  return <div className={`${className} ${flash ? "target-flash" : ""}`}>{children}</div>;
}
