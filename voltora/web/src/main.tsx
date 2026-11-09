import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "@/lib/auth";
import { ToastProvider } from "@/ui";
import { ApiError } from "@/lib/api";
import "@/ui/tokens.css";
import "@/ui/base.css";

const AdminApp = lazy(() => import("@/admin/AdminApp"));
const InstallWizard = lazy(() => import("@/install/InstallWizard"));
const StoreApp = lazy(() => import("@/store/StoreApp"));
const PreviewFrame = lazy(() => import("@/builder/PreviewFrame"));

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (n, e) => !(e instanceof ApiError && e.status >= 400 && e.status < 500) && n < 2,
      refetchOnWindowFocus: false,
      staleTime: 15_000,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <BrowserRouter>
          <AuthProvider>
            <Suspense fallback={null}>
              <Routes>
                <Route path="/install/*" element={<InstallWizard />} />
                <Route path="/admin/*" element={<AdminApp />} />
                <Route path="/__preview/*" element={<PreviewFrame />} />
                <Route path="/*" element={<StoreApp />} />
              </Routes>
            </Suspense>
          </AuthProvider>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
