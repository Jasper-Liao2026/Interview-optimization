import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "简历优化器",
  description: "批量生产岗位适配版简历：素材库 · JD 解析 · 并行改写 · 精调导出",
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
