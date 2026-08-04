import { useEffect, useRef, useState } from "react";

declare global {
  interface Window {
    adsbygoogle?: unknown[];
  }
}

type Props = {
  /** Numeric AdSense ad unit id (required when approved). */
  slot?: string;
  className?: string;
};

const CLIENT = (import.meta.env.VITE_ADSENSE_CLIENT as string | undefined)?.trim() || "";
const SLOT =
  (import.meta.env.VITE_ADSENSE_SLOT as string | undefined)?.trim() || "";
const APPROVED_RAW = String(import.meta.env.VITE_ADSENSE_APPROVED ?? "")
  .trim()
  .toLowerCase();
const APPROVED = APPROVED_RAW === "1" || APPROVED_RAW === "true" || APPROVED_RAW === "yes";

/** Only render real units after AdSense approval + env opt-in. */
export const ADSENSE_LIVE = Boolean(
  APPROVED && CLIENT.startsWith("ca-pub-") && /^\d+$/.test(SLOT),
);

let scriptLoading = false;

function ensureAdSenseScript() {
  if (!ADSENSE_LIVE || typeof document === "undefined") return;
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
 * Small footer AdSense unit. Renders nothing until
 * VITE_ADSENSE_APPROVED=true + client + numeric slot are set at build time.
 * Collapses until an iframe actually fills.
 */
export default function AdSlot({ slot, className = "" }: Props) {
  const hostRef = useRef<HTMLElement | null>(null);
  const pushed = useRef(false);
  const [filled, setFilled] = useState(false);
  const unitId = slot && /^\d+$/.test(slot) ? slot : SLOT;

  useEffect(() => {
    if (!ADSENSE_LIVE || !unitId) return;
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
  }, [unitId]);

  useEffect(() => {
    if (!ADSENSE_LIVE) return;
    const host = hostRef.current;
    if (!host) return;

    const check = () => {
      const iframe = host.querySelector("iframe");
      if (!iframe) return;
      const h = iframe.getBoundingClientRect().height;
      if (h >= 24) setFilled(true);
    };

    check();
    const obs = new MutationObserver(check);
    obs.observe(host, { childList: true, subtree: true });
    const poll = window.setInterval(check, 500);
    const stop = window.setTimeout(() => window.clearInterval(poll), 10000);
    return () => {
      obs.disconnect();
      window.clearInterval(poll);
      window.clearTimeout(stop);
    };
  }, []);

  if (!ADSENSE_LIVE || !unitId) {
    if (import.meta.env.DEV && APPROVED && !unitId) {
      return (
        <aside className={`ad-slot ad-slot--footer-sm ad-slot--placeholder ${className}`.trim()} aria-hidden="true">
          <span>AdSense approved but VITE_ADSENSE_SLOT missing</span>
        </aside>
      );
    }
    return null;
  }

  return (
    <aside
      ref={hostRef}
      className={`ad-slot ad-slot--footer-sm${filled ? " is-filled" : " is-pending"} ${className}`.trim()}
      aria-hidden={!filled}
    >
      <ins
        className="adsbygoogle"
        style={{ display: "block" }}
        data-ad-client={CLIENT}
        data-ad-slot={unitId}
        data-ad-format="horizontal"
        data-full-width-responsive="true"
      />
    </aside>
  );
}
