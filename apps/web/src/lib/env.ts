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
  apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
  appName: "简历优化器",
  apiPrefix: "/api/v1",
} as const;
