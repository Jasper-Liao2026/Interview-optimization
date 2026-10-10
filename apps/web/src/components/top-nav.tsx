"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/library", label: "素材库" },
  { href: "/jobs", label: "岗位匹配" },
  { href: "/generate", label: "生成简历" },
  { href: "/edit", label: "编辑简历" },
  { href: "/export", label: "批量导出" },
  { href: "/observability", label: "观测" },
  { href: "/", label: "系统自检" },
] as const;

export function TopNav() {
  const pathname = usePathname();

  return (
    <nav className="mb-8 flex flex-wrap items-center gap-1 border-b border-[var(--border)] pb-3">
      <span className="mr-3 font-mono text-xs tracking-widest text-[var(--muted)] uppercase">
        简历优化器
      </span>
      {LINKS.map((link) => {
        const active = pathname === link.href || (link.href === "/edit" && pathname.startsWith("/edit/"));
        return (
          <Link
            key={link.href}
            href={link.href}
            className="rounded-md px-3 py-1.5 text-xs transition-colors"
            style={{
              background: active ? "var(--panel-2)" : "transparent",
              color: active ? "var(--text)" : "var(--muted)",
            }}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
