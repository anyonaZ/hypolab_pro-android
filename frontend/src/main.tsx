import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./app/App";
import { installAndroidShims, installTouchMode } from "./lib/android";
import { applyUiScale } from "./lib/useAutoScale";
import "./styles/tokens.css";
import "./styles/global.css";
import "./styles/tablet.css";

// No-op in a browser; inside the HypoLab Android app it routes links, downloads and the
// clipboard through the native bridge.
installAndroidShims();
// Tablet layout (bigger touch targets, page scrolling, navigation drawer) on touch devices.
installTouchMode();
applyUiScale();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
