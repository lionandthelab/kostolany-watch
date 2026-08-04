import { Component, type ErrorInfo, type ReactNode } from "react";
import { trackEvent } from "./analytics";

type Props = {
  children: ReactNode;
  /** Identifies the failing region in analytics and in the fallback copy. */
  section: string;
  /** Shown instead of the section. Omit for the default one-line notice. */
  fallback?: ReactNode;
};

type State = { failed: boolean };

/**
 * Keeps one broken desk from blanking the whole app.
 *
 * Without this, a throw anywhere below the root takes down every section — a
 * malformed macro card would remove the regime call, which is the product. The
 * boundary is deliberately per-section rather than one root-level catch, so the
 * blast radius of a bad payload stays inside the section that received it.
 *
 * Deliberately has no "try again" button: these failures are render-time and
 * deterministic given the same payload, so a retry that re-renders identical
 * props would just fail again and read as a broken button.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Console keeps the stack for local debugging; the event tells us it
    // happened at all, since there is no error reporting service wired up.
    console.error(`[${this.props.section}]`, error, info.componentStack);
    try {
      trackEvent("section_error", {
        section: this.props.section,
        message: String(error?.message || error).slice(0, 120),
      });
    } catch {
      /* analytics must never be the reason a fallback fails to render */
    }
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    if (this.props.fallback !== undefined) return this.props.fallback;
    return (
      <p className="status" role="status">
        이 영역을 표시하지 못했습니다. 다른 영역은 정상입니다.
      </p>
    );
  }
}
