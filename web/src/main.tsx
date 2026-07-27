import React from "react";
import ReactDOM from "react-dom/client";
import { WebDemoApp } from "./app/WebDemoApp";
import "./styles/web-demo.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <WebDemoApp />
  </React.StrictMode>
);
