import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./app.css";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ThemeProvider } from "./lib/theme";
import { clientLog } from "./lib/logger";
import { BASE_PATH } from "./lib/base";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
});

window.addEventListener("error", (e) => {
  clientLog("client.error.unhandled", "error", { message: e.message, source: e.filename });
});
window.addEventListener("unhandledrejection", (e) => {
  clientLog("client.error.rejection", "error", { reason: String(e.reason).slice(0, 300) });
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter basename={BASE_PATH}>
          <ThemeProvider>
            <App />
          </ThemeProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>
);
