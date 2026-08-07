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

/**
 * Say out loud which env var is holding the units back.
 *
 * These are build-time constants, so "no ad anywhere on the site" and "the env
 * var was never set" look identical at runtime — the component simply returns
 * null and nothing distinguishes it from a page that has no slot. That cost a
 * full round of live debugging once (2026-08-07: units had never rendered
 * because .env carried only VITE_ADSENSE_CLIENT). DEV only; production stays
 * silent, and Vite folds `import.meta.env.DEV` to false so this whole block is
 * dropped from the shipped bundle.
 */
if (import.meta.env.DEV && !ADSENSE_LIVE) {
  const missing = [
    APPROVED ? "" : "VITE_ADSENSE_APPROVED=true",
    CLIENT.startsWith("ca-pub-") ? "" : "VITE_ADSENSE_CLIENT=ca-pub-…",
    /^\d+$/.test(SLOT) ? "" : "VITE_ADSENSE_SLOT=<digits>",
  ].filter(Boolean);
  console.info(
    `[AdSlot] ad units off — set ${missing.join(" + ")} in web/.env, then rebuild (build-time constants). See docs/ADSENSE.md`,
  );
}

let scriptLoading = false;

function ensureAdSenseScript() {
  if (!ADSENSE_LIVE || typeof document === "undefined") return;
  if (
    document.querySelector("script[data-adsense-loader]") ||
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
  // Marker for the duplicate-injection check below. NOT `data-adsense`: the
  // loader inspects its own script tag's dataset and logs "AdSense head tag
  // doesn't support data-adsense attribute" when it sees that name.
  s.dataset.adsenseLoader = "1";
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
      // An iframe is not evidence of an ad. AdSense inserts one either way and
      // then writes the outcome to `data-ad-status`; on an unfilled response it
      // is a 100px-tall empty frame, which the height test alone reads as a fill
      // and expands into a blank gap. Measured on the live site 2026-08-07:
      // status="done", ad-status="unfilled", iframe 448x100.
      const ins = host.querySelector("ins.adsbygoogle");
      if (ins?.getAttribute("data-ad-status") === "unfilled") {
        setFilled(false);
        return;
      }
      const iframe = host.querySelector("iframe");
      if (!iframe) return;
      if (iframe.getBoundingClientRect().height >= 24) setFilled(true);
    };

    check();
    const obs = new MutationObserver(check);
    // `data-ad-status` arrives as an attribute write, not a child insertion, so
    // childList alone would leave the collapse waiting on the 500ms poll.
    obs.observe(host, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["data-ad-status"],
    });
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
