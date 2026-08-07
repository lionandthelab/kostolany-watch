import { LandingEgg } from "./EggChart";
import { CYCLE, REGIME_GUIDE, TOP_MODELS } from "./eggGeometry";
import { useT } from "./i18n";
import LocaleSwitcher from "./LocaleSwitcher";
import AdSlot from "./AdSlot";
import PushOptIn from "./PushOptIn";

type Props = {
  onEnter: () => void;
  onMacro?: () => void;
  onGuide?: () => void;
  onHome?: () => void;
};

export default function Landing({ onEnter, onMacro, onGuide }: Props) {
  const t = useT();

  return (
    <div className="landing">
      <div className="landing-lang">
        <LocaleSwitcher />
      </div>
      <section className="landing-hero">
        <div className="landing-hero-copy fade-up">
          <h1 className="brand landing-brand">Kostolany Watch</h1>
          <p className="landing-headline">{t.landing.headline}</p>
          <p className="landing-sub">{t.landing.sub}</p>
          <div className="landing-cta">
            <button type="button" className="btn-primary" onClick={onEnter}>
              {t.landing.ctaWatch}
            </button>
            {onMacro && (
              <button type="button" className="btn-ghost" onClick={onMacro}>
                {t.landing.ctaMacro}
              </button>
            )}
            {onGuide && (
              /* An <a href>, not a button, on purpose: the landing is the most
                 linked page on the site and the guide hub had no inbound link a
                 crawler could follow — a button is not one. preventDefault keeps
                 SPA navigation, and modified clicks fall through to the browser
                 so "open in new tab" still works. */
              <a
                className="btn-ghost"
                href="/guide/"
                onClick={(e) => {
                  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                  e.preventDefault();
                  onGuide();
                }}
              >
                {t.nav.guide}
              </a>
            )}
          </div>
        </div>

        <div className="landing-hero-visual fade-up" aria-hidden="true">
          <div className="landing-egg-bleed">
            <LandingEgg />
          </div>
        </div>
      </section>

      <section id="about" className="landing-section">
        <h2>{t.landing.whatTitle}</h2>
        <p>{t.landing.whatBody}</p>
      </section>

      <section className="landing-section">
        <h2>{t.landing.sixTitle}</h2>
        <p className="landing-section-lead">{t.landing.sixLead}</p>
        <ul className="regime-list">
          {CYCLE.map((code) => {
            const color = REGIME_GUIDE[code].color;
            const r = t.regimes[code];
            return (
              <li key={code}>
                <span className="regime-code" style={{ color }}>
                  {code}
                </span>
                <span className="regime-name">{r.name}</span>
                <span className="regime-action">{r.action}</span>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="landing-section">
        <h2>{t.landing.analystsTitle}</h2>
        <ul className="analyst-list">
          {TOP_MODELS.map((m) => {
            const copy = t.models[m.id];
            return (
              <li key={m.id}>
                <span className="analyst-dot" style={{ background: m.color }} />
                <div>
                  <strong style={{ color: m.color }}>{copy.label}</strong>
                  <em>{copy.trait}</em>
                  <p>{copy.blurb}</p>
                </div>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="landing-section landing-finale">
        <h2>{t.landing.finaleTitle}</h2>
        <p>{t.landing.finaleBody}</p>
        <button type="button" className="btn-primary" onClick={onEnter}>
          {t.landing.ctaWatch}
        </button>
        <PushOptIn source="landing" className="push-optin--landing" />
        <p className="disclaimer landing-disclaimer">{t.landing.disclaimer}</p>
        <AdSlot />
      </section>
    </div>
  );
}
