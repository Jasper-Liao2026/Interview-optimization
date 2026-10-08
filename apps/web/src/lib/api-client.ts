/**
 * 类型化 API 客户端。
 *
 * 硬约定（tech-stack.md §6.1）：
 *   Pydantic model 是接口类型的**单一定义源**，前端类型一律从
 *   `@resume/api-types`（openapi-typescript 生成产物）取出，**禁止手写**。
 *
 * 任何接口改动 → 先改 apps/api 的 Pydantic model → 跑 `pnpm gen:types`。
 */
import type { components } from "@resume/api-types";

import { env } from "./env";

export type HealthResponse = components["schemas"]["HealthResponse"];
export type SystemInfoResponse = components["schemas"]["SystemInfoResponse"];
export type ServiceMetaEntry = components["schemas"]["ServiceMetaEntry"];
export type DatabaseStatus = components["schemas"]["DatabaseStatus"];

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly path: string,
    readonly body: string,
  ) {
    super(`API ${status} ${path}`);
    this.name = "ApiError";
  }
}

type RequestOptions = {
  path: string;
  /** 贯穿前后端的 trace_id：后端会原样回写 X-Request-Id */
  requestId?: string;
  signal?: AbortSignal;
};

/**
 * 生成 trace_id 并随请求下发。
 * 对应 tech-stack.md「代价四」的缓解措施：Next.js 生成请求 ID 透传给 FastAPI，
 * 两边日志与后续 Langfuse trace 用同一个 ID。
 */
export function newRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `web-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

export async function apiGet<T>({ path, requestId, signal }: RequestOptions): Promise<T> {
  const response = await fetch(`${env.apiBaseUrl}${path}`, {
    method: "GET",
    headers: {
      Accept: "application/json",
      ...(requestId ? { "X-Request-Id": requestId } : {}),
    },
    // 健康检查必须实时，不能被 Next 的 fetch 缓存吃掉
    cache: "no-store",
    signal,
  });

  if (!response.ok) {
    throw new ApiError(response.status, path, await response.text().catch(() => ""));
  }
  return (await response.json()) as T;
}

export const api = {
  health: (opts?: Omit<RequestOptions, "path">) =>
    apiGet<HealthResponse>({ path: `${env.apiPrefix}/health`, ...opts }),
  systemInfo: (opts?: Omit<RequestOptions, "path">) =>
    apiGet<SystemInfoResponse>({ path: `${env.apiPrefix}/system/info`, ...opts }),
};
