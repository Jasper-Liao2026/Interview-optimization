import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "简历优化器 · 系统自检台",
  description: "M0 脚手架联通验证页：前端 → FastAPI → Postgres 全链路自检",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
