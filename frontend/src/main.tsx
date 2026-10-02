import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { makeQueryClient } from "./api/queries";
import "./styles/global.css";

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");

createRoot(root).render(
  <StrictMode>
    <App queryClient={makeQueryClient()} />
  </StrictMode>,
);
