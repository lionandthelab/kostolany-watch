import { useCallback, useEffect, useState } from "react";
import Landing from "./Landing";
import WatchApp from "./WatchApp";

type Mode = "landing" | "watch";

function modeFromHash(): Mode {
  return window.location.hash === "#watch" ? "watch" : "landing";
}

export default function App() {
  const [mode, setMode] = useState<Mode>(() =>
    typeof window !== "undefined" ? modeFromHash() : "landing",
  );

  const enter = useCallback(() => {
    setMode("watch");
    window.history.replaceState(null, "", "#watch");
    window.scrollTo(0, 0);
  }, []);

  const back = useCallback(() => {
    setMode("landing");
    window.history.replaceState(null, "", window.location.pathname);
    window.scrollTo(0, 0);
  }, []);

  useEffect(() => {
    const onHash = () => setMode(modeFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  if (mode === "watch") {
    return <WatchApp onBack={back} />;
  }
  return <Landing onEnter={enter} />;
}
