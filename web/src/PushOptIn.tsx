import { useEffect, useState, type FormEvent } from "react";
import { pushSubscribe, pushUnsubscribe, fetchVapidPublicKey } from "./api";
import { trackEvent } from "./analytics";
import { useLocale, useT } from "./i18n";

type Props = {
  source?: string;
  className?: string;
};

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function ensureSw(): Promise<ServiceWorkerRegistration | null> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return null;
  return navigator.serviceWorker.register("/sw.js", { scope: "/" });
}

export default function PushOptIn({ source = "web", className = "" }: Props) {
  const t = useT();
  const { locale } = useLocale();
  const [supported, setSupported] = useState(true);
  const [enabled, setEnabled] = useState(false);
  const [hour, setHour] = useState(22);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
        if (!cancelled) setSupported(false);
        return;
      }
      try {
        const reg = await ensureSw();
        const sub = await reg?.pushManager.getSubscription();
        if (!cancelled) setEnabled(Boolean(sub));
        const saved = localStorage.getItem("kw_push_hour");
        if (saved && !cancelled) setHour(Number(saved) || 22);
      } catch {
        if (!cancelled) setSupported(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function enable() {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const pub = await fetchVapidPublicKey();
      if (!pub) throw new Error("vapid_unavailable");
      const permission = await Notification.requestPermission();
      if (permission !== "granted") throw new Error("denied");
      const reg = await ensureSw();
      if (!reg) throw new Error("sw_unavailable");
      await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(pub) as BufferSource,
        });
      }
      const json = sub.toJSON();
      await pushSubscribe({
        endpoint: json.endpoint || "",
        keys: {
          p256dh: json.keys?.p256dh || "",
          auth: json.keys?.auth || "",
        },
        hour_kst: hour,
        locale,
      });
      localStorage.setItem("kw_push_hour", String(hour));
      setEnabled(true);
      setMsg(t.push.success);
      trackEvent("push_subscribe", { status: "ok", source, hour });
    } catch (ex) {
      const detail = ex instanceof Error ? ex.message : "";
      if (detail.includes("denied")) setErr(t.push.denied);
      else if (detail.includes("vapid")) setErr(t.push.unavailable);
      else setErr(t.push.error);
      trackEvent("push_subscribe", { status: "error", source });
    } finally {
      setBusy(false);
    }
  }

  async function disable() {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const reg = await navigator.serviceWorker.getRegistration();
      const sub = await reg?.pushManager.getSubscription();
      if (sub) {
        await pushUnsubscribe(sub.endpoint);
        await sub.unsubscribe();
      }
      setEnabled(false);
      setMsg(t.push.off);
      trackEvent("push_unsubscribe", { source });
    } catch {
      setErr(t.push.error);
    } finally {
      setBusy(false);
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (enabled) await disable();
    else await enable();
  }

  if (!supported) {
    return (
      <section className={`push-optin ${className}`.trim()}>
        <h3>{t.push.title}</h3>
        <p className="push-lead">{t.push.unsupported}</p>
      </section>
    );
  }

  return (
    <section className={`push-optin ${className}`.trim()} aria-labelledby="push-title">
      <h3 id="push-title">{t.push.title}</h3>
      <p className="push-lead">{t.push.lead}</p>
      <form className="push-form" onSubmit={onSubmit}>
        <label className="push-hour-label" htmlFor="push-hour">
          {t.push.hourLabel}
        </label>
        <select
          id="push-hour"
          value={hour}
          disabled={busy || enabled}
          onChange={(ev) => setHour(Number(ev.target.value))}
        >
          {Array.from({ length: 24 }, (_, h) => (
            <option key={h} value={h}>
              {String(h).padStart(2, "0")}:00 KST
            </option>
          ))}
        </select>
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? t.push.busy : enabled ? t.push.disable : t.push.enable}
        </button>
      </form>
      {msg && (
        <p className="push-msg" role="status">
          {msg}
        </p>
      )}
      {err && (
        <p className="push-err" role="alert">
          {err}
        </p>
      )}
      <p className="push-note">{t.push.note}</p>
    </section>
  );
}
