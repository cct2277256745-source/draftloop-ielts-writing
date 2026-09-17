import React from "react";
import { createRoot } from "react-dom/client";
import { LiveWorkspace } from "./LiveWorkspace";
import "./styles.css";
import "./live.css";
import "./writing-studio.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <LiveWorkspace />
  </React.StrictMode>,
);
