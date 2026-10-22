import type { Metadata } from "next";
import AppShell from "@/components/AppShell";
import "./globals.css";
import "./workspace-foundation.css";
import "./workspace-projects.css";
import "./workspace-settings.css";
import "./workspace-studio.css";
import "./ip-chapter-picker.css";
import "./design-tokens.css";
import "./experience-common.css";
import "./experience-projects.css";
import "./experience-home.css";
import "./experience-studio.css";
import "./experience-settings.css";
import "./experience-ip.css";
import "./brand.css";
import "./experience-flow.css";

export const metadata: Metadata = {
  title: "悦创 · YueChuang",
  description: "AI视频生成工具",
  icons: {
    icon: [{ url: '/brand/yuechuang-symbol-v5.svg', type: 'image/svg+xml' }],
    apple: [{ url: '/brand/apple-touch-icon-v5.png', sizes: '180x180', type: 'image/png' }],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased" suppressHydrationWarning>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
