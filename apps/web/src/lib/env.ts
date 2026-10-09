/**
 * 前端运行时配置。
 * 约定：只有 NEXT_PUBLIC_ 前缀的变量会注入浏览器。
 */
export const env = {
  /**
   * 后端地址。
   * 依据 tech-stack.md「代价二」的缓解措施：流式端点由浏览器直连 FastAPI，
   * 不经 Next.js 代理转发。因此这里在前端侧直接持有后端基址。
   */
  // 刻意用 127.0.0.1 而非 localhost：
  // Windows 上 localhost 会被浏览器优先解析成 IPv6 的 ::1，而后端 uvicorn
  // 用 --host 127.0.0.1 启动时只监听 IPv4，导致浏览器 fetch 直接 Failed to fetch。
  // 写死 IPv4 直连，与页面本身用 localhost 还是 127.0.0.1 打开解耦。
  apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000",
  appName: "简历优化器",
  apiPrefix: "/api/v1",
} as const;
