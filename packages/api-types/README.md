# @resume/api-types

**前端接口类型的唯一来源。任何手写的接口类型都是 bug。**

```
apps/api/app/schemas/*.py        ← 单一定义源（Pydantic model）
        │
        │  FastAPI 自动产出 OpenAPI schema
        ▼
apps/api/openapi/openapi.json    ← 中间产物（不入库）
        │
        │  openapi-typescript
        ▼
packages/api-types/src/schema.d.ts   ← 生成产物（入库）
```

## 用法

```bash
pnpm install
pnpm gen:types        # 或 --check 模式用于 CI
```

```ts
import type { components } from "@resume/api-types";

type HealthResponse = components["schemas"]["HealthResponse"];
```

## 为什么 schema.d.ts 要入库

直觉上生成产物不该入库，但这里是有意为之：

1. **前端可以脱离 Python 环境构建** —— clone 下来 `pnpm install && pnpm build` 就能跑，不必先装 uv 与 Python
2. **纯 Node 构建环境不需要 Python** —— 少一项构建依赖就少一个失败点
3. **前后端可并行开发** —— 后端定义了 schema 并推上来，前端立刻能用

同步性由 CI 保证：拉取代码后重新执行 `pnpm gen:types`，若工作区出现 diff 说明有人改了 Pydantic 却没刷新类型，直接失败。

## 硬约定

- 接口变更**先改 `apps/api/app/schemas/` 下的 Pydantic model**
- 生成后提交 `schema.d.ts`
- 前端禁止出现手写的接口字段定义
