import { useCallback, useEffect, useState } from "react";
import FlowsDesk from "./FlowsDesk";
import Landing from "./Landing";
import NewsDesk from "./NewsDesk";
import WatchApp from "./WatchApp";

type Mode = "landing" | "watch" | "news" | "flows";

function modeFromHash(): Mode {
  const h = window.location.hash;
  if (h === "#news") return "news";
  if (h === "#flows") return "flows";
  if (h === "#watch") return "watch";
  return "landing";
}

export default function App() {
  const [mode, setMode] = useState<Mode>(() =>
    typeof window !== "undefined" ? modeFromHash() : "landing",
  );

  const enterWatch = useCallback(() => {
    setMode("watch");
    window.history.replaceState(null, "", "#watch");
    window.scrollTo(0, 0);
  }, []);

  const enterNews = useCallback(() => {
    setMode("news");
    window.history.replaceState(null, "", "#news");
    window.scrollTo(0, 0);
  }, []);

  const enterFlows = useCallback(() => {
    setMode("flows");
    window.history.replaceState(null, "", "#flows");
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
    return <WatchApp onBack={back} onNews={enterNews} onFlows={enterFlows} />;
  }
  if (mode === "news") {
    return <NewsDesk onBack={back} onWatch={enterWatch} onFlows={enterFlows} />;
  }
  if (mode === "flows") {
    return <FlowsDesk onBack={back} onWatch={enterWatch} onNews={enterNews} />;
  }
  return <Landing onEnter={enterWatch} onNews={enterNews} onFlows={enterFlows} />;
}
