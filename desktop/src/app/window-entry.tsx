import { App } from "./App";
import { VoiceWindowApp } from "./VoiceWindowApp";

export function WindowEntry() {
  if (window.location.hash === "#/voice") {
    return <VoiceWindowApp />;
  }

  return <App />;
}
