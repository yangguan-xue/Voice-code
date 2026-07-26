import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { WindowEntry } from "./app/window-entry";
import "./styles/tokens.css";
import "./styles/app.css";
import "./styles/voice.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("Root element #root was not found");
}

createRoot(root).render(
  <StrictMode>
    <WindowEntry />
  </StrictMode>
);
