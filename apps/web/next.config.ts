import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Docker 部署用 standalone 产物，镜像体积和启动时间都更可控
  output: "standalone",
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
