/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ADSENSE_CLIENT?: string;
  /** Set true only after AdSense site/ad-unit approval. */
  readonly VITE_ADSENSE_APPROVED?: string;
  /** Numeric ad unit id for the small footer banner. */
  readonly VITE_ADSENSE_SLOT?: string;
  readonly VITE_GA_MEASUREMENT_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
