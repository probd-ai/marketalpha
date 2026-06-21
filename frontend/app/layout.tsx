import "./globals.css";
import type { Metadata } from "next";
import { Providers } from "./providers";
import { Nav } from "@/components/nav";

export const metadata: Metadata = { title: "tvagent — Live Portfolio" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <div style={{ display: "flex", minHeight: "100vh" }}>
            <Nav />
            <main style={{ flex: 1, padding: "18px 22px", maxWidth: 1500, margin: "0 auto" }}>
              {children}
            </main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
