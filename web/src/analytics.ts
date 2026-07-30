/** Google Analytics 4 — SPA page views + product events. */

export type AnalyticsParams = Record<string, string | number | boolean | undefined>;

declare global {
  interface Window {
    dataLayer?: IArguments[];
    gtag?: (...args: unknown[]) => void;
  }
}

const MEASUREMENT_ID = (import.meta.env.VITE_GA_MEASUREMENT_ID as string | undefined)?.trim() || "";
export const analyticsEnabled = Boolean(MEASUREMENT_ID && MEASUREMENT_ID.startsWith("G-"));

let initialized = false;

/**
 * Official gtag bootstrap: must push `arguments` (not a rest-array),
 * or GA4 silently drops hits and Realtime stays empty.
 */
export function initAnalytics() {
  if (!analyticsEnabled || initialized || typeof window === "undefined") return;
  initialized = true;

  window.dataLayer = window.dataLayer || [];
  // Classic function required so `arguments` is the Arguments object gtag expects.
  window.gtag = function gtag() {
    // eslint-disable-next-line prefer-rest-params
    window.dataLayer!.push(arguments);
  };

  window.gtag("js", new Date());
  window.gtag("config", MEASUREMENT_ID, {
    send_page_view: false,
  });

  if (!document.querySelector(`script[src*="googletagmanager.com/gtag/js"]`)) {
    const s = document.createElement("script");
    s.async = true;
    s.src = `https://www.googletagmanager.com/gtag/js?id=${MEASUREMENT_ID}`;
    document.head.appendChild(s);
  }
}

export function trackPageView(path: string, title?: string) {
  if (!analyticsEnabled || !window.gtag) return;
  const pageTitle = title ?? document.title;
  const pageLocation = `${window.location.origin}${path}${window.location.search}`;
  // GA4 SPA recommendation: update config path + explicit page_view
  window.gtag("config", MEASUREMENT_ID, {
    page_path: path,
    page_title: pageTitle,
    page_location: pageLocation,
  });
  window.gtag("event", "page_view", {
    page_path: path,
    page_title: pageTitle,
    page_location: pageLocation,
  });
}

export function trackEvent(name: string, params?: AnalyticsParams) {
  if (!analyticsEnabled || !window.gtag) return;
  const clean: Record<string, string | number | boolean> = {};
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined) clean[k] = v;
    }
  }
  window.gtag("event", name, clean);
}
