import { useState, type FormEvent } from "react";
import { subscribeNewsletter } from "./api";
import { trackEvent } from "./analytics";
import { useLocale, useT } from "./i18n";

type Props = {
  source?: string;
  className?: string;
};

export default function NewsletterSignup({ source = "guide", className = "" }: Props) {
  const t = useT();
  const { locale } = useLocale();
  const [email, setEmail] = useState("");
  const [honeypot, setHoneypot] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const res = await subscribeNewsletter(email, {
        locale,
        source,
        website: honeypot,
      });
      trackEvent("newsletter_subscribe", { status: res.status, source });
      if (res.status === "already") setMsg(t.newsletter.already);
      else setMsg(t.newsletter.success);
      setEmail("");
    } catch (ex) {
      const detail = ex instanceof Error ? ex.message : "";
      if (detail.includes("invalid")) setErr(t.newsletter.invalid);
      else if (detail.includes("429") || detail.includes("too_many")) setErr(t.newsletter.rateLimited);
      else setErr(t.newsletter.error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={`newsletter-signup ${className}`.trim()} aria-labelledby="newsletter-title">
      <h3 id="newsletter-title">{t.newsletter.title}</h3>
      <p className="newsletter-lead">{t.newsletter.lead}</p>
      <form className="newsletter-form" onSubmit={onSubmit}>
        <label className="sr-only" htmlFor="newsletter-email">
          {t.newsletter.emailLabel}
        </label>
        <input
          id="newsletter-email"
          type="email"
          name="email"
          autoComplete="email"
          required
          placeholder={t.newsletter.placeholder}
          value={email}
          onChange={(ev) => setEmail(ev.target.value)}
          disabled={busy}
        />
        <input
          className="newsletter-hp"
          type="text"
          name="website"
          tabIndex={-1}
          autoComplete="off"
          aria-hidden="true"
          value={honeypot}
          onChange={(ev) => setHoneypot(ev.target.value)}
        />
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? t.newsletter.submitting : t.newsletter.submit}
        </button>
      </form>
      {msg && <p className="newsletter-msg" role="status">{msg}</p>}
      {err && (
        <p className="newsletter-err" role="alert">
          {err}
        </p>
      )}
      <p className="newsletter-note">{t.newsletter.note}</p>
    </section>
  );
}
