import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server on http://localhost:5173 (the simulator is a separate page).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
