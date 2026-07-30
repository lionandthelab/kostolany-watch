import { useEffect, useRef } from "react";

declare global {
  interface Window {
    adsbygoogle?: unknown[];
  }
}

type Props = {
  /** Logical slot name for future AdSense mapping */
  slot?: string;
  className?: string;
  /** Min height so layout does not jump when ads load */
  minHeight?: number;
};

const CLIENT = (import.meta.env.VITE_ADSENSE_CLIENT as string | undefined)?.trim() || "";
const ENABLED = Boolean(CLIENT && CLIENT.startsWith("ca-pub-"));

let scriptLoading = false;

function ensureAdSenseScript() {
  if (!ENABLED || typeof document === "undefined") return;
  if (
    document.querySelector("script[data-adsense]") ||
    document.querySelector('script[src*="pagead2.googlesyndication.com"]')
  ) {
    return;
  }
  if (scriptLoading) return;
  scriptLoading = true;
  const s = document.createElement("script");
  s.async = true;
  s.src = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${CLIENT}`;
  s.crossOrigin = "anonymous";
  s.dataset.adsense = "1";
  document.head.appendChild(s);
}

/**
 * AdSense unit. Uses auto format until numeric slot IDs are configured in AdSense.
 * Script client comes from VITE_ADSENSE_CLIENT (and index.html for verification).
 */
export default function AdSlot({ slot = "auto", className = "", minHeight = 90 }: Props) {
  const pushed = useRef(false);
  const adSlotId = /^\d+$/.test(slot) ? slot : undefined;

  useEffect(() => {
    if (!ENABLED) return;
    ensureAdSenseScript();
    if (pushed.current) return;
    const t = window.setTimeout(() => {
      try {
        (window.adsbygoogle = window.adsbygoogle || []).push({});
        pushed.current = true;
      } catch {
        /* ignore until script ready */
      }
    }, 50);
    return () => window.clearTimeout(t);
  }, [slot]);

  if (!ENABLED) {
    if (!import.meta.env.DEV) return null;
    return (
      <aside
        className={`ad-slot ad-slot--placeholder ${className}`.trim()}
        style={{ minHeight }}
        aria-hidden="true"
      >
        <span>Ad slot (set VITE_ADSENSE_CLIENT)</span>
      </aside>
    );
  }

  return (
    <aside className={`ad-slot ${className}`.trim()} style={{ minHeight }}>
      <ins
        className="adsbygoogle"
        style={{ display: "block" }}
        data-ad-client={CLIENT}
        {...(adSlotId ? { "data-ad-slot": adSlotId } : {})}
        data-ad-format="auto"
        data-full-width-responsive="true"
      />
    </aside>
  );
}
