import type { Metadata, Viewport } from "next";

export const metadata: Metadata = {
  title: "NEROCLAUDE Dashboard",
  description: "Polymarket trading bot position tracker",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <style>{`
          @media (max-width: 768px) {
            .desktop-only { display: none !important; }
            .mobile-stack { flex-direction: column !important; }
            .mobile-full { width: 100% !important; }
            .mobile-small-text { font-size: 12px !important; }
            .mobile-gap { gap: 8px !important; }
          }
        `}</style>
      </head>
      <body
        style={{
          margin: 0,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace',
          backgroundColor: "#0a0a0f",
          color: "#e0e0e0",
          minHeight: "100vh",
          overflowX: "hidden",
        }}
      >
        {children}
      </body>
    </html>
  );
}
