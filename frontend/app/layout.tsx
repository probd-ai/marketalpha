import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Shell } from "@/components/Shell";

import "./globals.css";

export const metadata: Metadata = { title: "Fund Desk · tvagent", description: "Model-portfolio trading desk" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="font-sans antialiased">
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
