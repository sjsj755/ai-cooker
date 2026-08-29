/**
 * 请求层：任务级幂等中断（AbortController 按任务类型映射）+ 5s 超时 + 错误归一。
 * 每个页面脚本创建自己的 createTaskRegistry() 实例，页面间不共享 controller。
 * 安全约定：本文件与页面脚本一律使用 createElement + textContent 渲染文本，
 * 不拼接 HTML 字符串，不执行动态脚本。
 */
"use strict";

const REQUEST_TIMEOUT_MS = 5000;

/** 归一化后的前端请求错误。 */
class ApiError extends Error {
  /**
   * @param {"aborted"|"timeout"|"http"|"server"|"network"} type 错误分类
   * @param {string} message 用户可读信息
   * @param {number|null} status HTTP 状态码（网络层错误为 null）
   */
  constructor(type, message, status = null) {
    super(message);
    this.name = "ApiError";
    this.type = type;
    this.status = status;
  }
}

/**
 * 任务级请求注册表：每个任务类型同一时刻至多一个在途请求。
 * - abort(taskType)：中断该任务类型的在途请求（对已完成请求无副作用）；
 * - run(taskType, fetcher, timeoutMs?)：先幂等中断同任务在途请求，再发起新请求；
 *   默认 5s 超时自动 abort（LLM 重任务如 recommend 可传更大 timeoutMs 覆盖）；
 *   finally 仅当映射仍指向本次 controller 才移除（防陈旧引用覆盖新请求）。
 */
function createTaskRegistry() {
  const controllers = new Map();

  function abort(taskType) {
    const controller = controllers.get(taskType);
    if (controller) {
      controller.abort();
    }
  }

  async function run(taskType, fetcher, timeoutMs = REQUEST_TIMEOUT_MS) {
    abort(taskType);
    const controller = new AbortController();
    controllers.set(taskType, controller);
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    try {
      return await fetcher(controller.signal);
    } catch (err) {
      if (err && err.name === "AbortError") {
        throw new ApiError(
          timedOut ? "timeout" : "aborted",
          timedOut ? "请求超时，请重试" : "请求已中断"
        );
      }
      if (err instanceof TypeError) {
        // fetch 网络层失败（断网 / DNS / 连接拒绝）
        throw new ApiError("network", "网络连接失败，请检查网络后重试");
      }
      throw err;
    } finally {
      clearTimeout(timer);
      if (controllers.get(taskType) === controller) {
        controllers.delete(taskType);
      }
    }
  }

  return { abort, run };
}

/** 从 FastAPI 错误体中提取可展示信息（detail 可能是字符串或校验错误数组）。 */
function detailMessage(body, fallback) {
  if (!body) {
    return fallback;
  }
  const detail = body.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => (item && typeof item.msg === "string" ? item.msg : ""))
      .filter(Boolean);
    if (parts.length) {
      return parts.join("；");
    }
  }
  return fallback;
}

/** 发 JSON 请求并归一非 2xx 错误：4xx 展示后端 detail，5xx 服务暂不可用。 */
async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  let body = null;
  if (contentType.includes("application/json")) {
    body = await response.json().catch(() => null);
  }
  if (!response.ok) {
    if (response.status >= 500) {
      throw new ApiError(
        "server",
        detailMessage(body, "服务暂不可用，请稍后重试"),
        response.status
      );
    }
    throw new ApiError(
      "http",
      detailMessage(body, `请求失败（HTTP ${response.status}）`),
      response.status
    );
  }
  return body;
}

/** 把参数对象拼为 query string（跳过空值），无参数返回空串。 */
function buildQuery(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

window.Api = {
  REQUEST_TIMEOUT_MS,
  ApiError,
  createTaskRegistry,
  requestJson,
  buildQuery,
};
