import { useLocale } from "./i18n";
import { trackEvent } from "./analytics";

/** Compact EN/KO switcher; reserved locales stay hidden until enabled. */
export default function LocaleSwitcher() {
  const { locale, setLocale, enabledLocales, t } = useLocale();
  const options = enabledLocales.filter((m) => m.enabled);

  return (
    <label className="locale-switch">
      <span className="visually-hidden">{t.nav.language}</span>
      <select
        value={locale}
        aria-label={t.nav.language}
        onChange={(e) => {
          const next = e.target.value as typeof locale;
          setLocale(next);
          trackEvent("language_change", { locale: next, from: locale });
        }}
      >
        {options.map((m) => (
          <option key={m.id} value={m.id}>
            {m.nativeLabel}
          </option>
        ))}
      </select>
    </label>
  );
}
