import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/app";
import "./index.css";

function ServiceWorkerRegistration() {
  useEffect(() => {
    if ("serviceWorker" in navigator) {
      void navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
        // Installation is optional; a registration failure must not block learning.
      });
    }
  }, []);
  return null;
}

const rootElement = document.getElementById("root");
if (rootElement) {
  createRoot(rootElement).render(
    <StrictMode>
      <App />
      <ServiceWorkerRegistration />
    </StrictMode>,
  );
}
