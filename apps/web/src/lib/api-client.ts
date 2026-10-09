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

// --- M1 垂直切片 ---
export type ExperienceRead = components["schemas"]["ExperienceRead"];
export type ExperienceListResponse = components["schemas"]["ExperienceListResponse"];
export type GenerateRequest = components["schemas"]["GenerateRequest"];
export type GenerateResponse = components["schemas"]["GenerateResponse"];
export type JobProfile = components["schemas"]["JobProfile"];
export type ResumeRead = components["schemas"]["ResumeRead"];
export type ResumeSection = components["schemas"]["ResumeSection"];

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

async function handle<T>(response: Response, path: string): Promise<T> {
  if (!response.ok) {
    throw new ApiError(response.status, path, await response.text().catch(() => ""));
  }
  return (await response.json()) as T;
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
  return handle<T>(response, path);
}

export async function apiPost<T>(
  { path, requestId, signal }: RequestOptions,
  body: unknown,
): Promise<T> {
  const response = await fetch(`${env.apiBaseUrl}${path}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(requestId ? { "X-Request-Id": requestId } : {}),
    },
    body: JSON.stringify(body),
    cache: "no-store",
    signal,
  });
  return handle<T>(response, path);
}

/** 取回服务端渲染的简历 HTML 文本（用于浏览器打印：srcdoc + window.print）。 */
export async function fetchResumeHtml(resumeId: string, signal?: AbortSignal): Promise<string> {
  const response = await fetch(`${env.apiBaseUrl}${api.resumeHtmlPath(resumeId)}`, {
    headers: { Accept: "text/html" },
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new ApiError(response.status, api.resumeHtmlPath(resumeId), await response.text());
  }
  return response.text();
}

export const api = {
  health: (opts?: Omit<RequestOptions, "path">) =>
    apiGet<HealthResponse>({ path: `${env.apiPrefix}/health`, ...opts }),
  systemInfo: (opts?: Omit<RequestOptions, "path">) =>
    apiGet<SystemInfoResponse>({ path: `${env.apiPrefix}/system/info`, ...opts }),

  // --- M1 ---
  listExperiences: (opts?: Omit<RequestOptions, "path">) =>
    apiGet<ExperienceListResponse>({ path: `${env.apiPrefix}/experiences`, ...opts }),
  generateResume: (body: GenerateRequest, opts?: Omit<RequestOptions, "path">) =>
    apiPost<GenerateResponse>({ path: `${env.apiPrefix}/resumes/generate`, ...opts }, body),

  /**
   * 后端返回的 preview_path / pdf_path 是**相对 API 前缀**的路径，
   * 前端统一拼上 apiBaseUrl。之所以不让后端返回绝对 URL：
   * 同一份后端在本地、容器、生产里域名都不同，绝对 URL 只会写死一个环境。
   */
  resumeHtmlPath: (resumeId: string) => `${env.apiPrefix}/resumes/${resumeId}/html`,
  resumePdfPath: (resumeId: string, download = false) =>
    `${env.apiPrefix}/resumes/${resumeId}/pdf${download ? "?download=1" : ""}`,
  resumeHtmlUrl: (resumeId: string) => `${env.apiBaseUrl}${api.resumeHtmlPath(resumeId)}`,
  resumePdfUrl: (resumeId: string, download = false) =>
    `${env.apiBaseUrl}${api.resumePdfPath(resumeId, download)}`,
};
