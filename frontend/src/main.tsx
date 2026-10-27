import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./app/App";
import { installAndroidShims } from "./lib/android";
import "./styles/tokens.css";
import "./styles/global.css";

// No-op in a browser; inside the HypoLab Android app it routes links, downloads and the
// clipboard through the native bridge.
installAndroidShims();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
