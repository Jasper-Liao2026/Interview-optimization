import type { NextConfig } from "next";
import { resolve } from "node:path";

const nextConfig: NextConfig = {
  // Linux/Docker 使用 standalone；Windows 本地构建避免其符号链接权限限制。
  output: process.platform === "win32" ? undefined : "standalone",
  outputFileTracingRoot: resolve(__dirname, "../.."),
  reactStrictMode: true,
  // 注意：这里刻意不写 transpilePackages: ["@resume/api-types"]。
  // 该包是**纯类型包**（只有 .d.ts 与 `export type`），
  // 前端一律用 `import type` 引入，编译期即被擦除，运行时不需要打包它。
  // 引用方式走 tsconfig 的 paths 别名，因此也不必是 workspace dependency
  // —— 少一条依赖就少一处链接与版本对齐的麻烦。
  // Windows + Docker 挂载卷下，轮询比 inotify 更可靠
  webpack: (config, { dev }) => {
    if (dev) {
      config.watchOptions = { poll: 1000, aggregateTimeout: 300 };
    }
    return config;
  },
};

export default nextConfig;
