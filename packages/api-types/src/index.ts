/**
 * OpenAPI → TypeScript 类型出口。
 *
 * 本文件只是转发，不放任何手写类型定义。
 * 具体类型全部来自 `./schema`（openapi-typescript 生成）。
 *
 * 若 IDE 报「找不到 ./schema」，说明生成产物缺失，跑一次：
 *     pnpm gen:types
 */
export type { components, operations, paths } from "./schema";
