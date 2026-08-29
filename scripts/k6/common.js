// k6 公共配置（P5 §3.8）：BASE_URL 与阈值模板。
// 用法：k6 run scripts/k6/search.js（压测前关闭限流 RATE_LIMIT_ENABLED=false；
// 429 效果用 rate_limit.js 单独验证）。
export const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8000";

// 性能门禁模板（P5 §3.7）：P95 毫秒 + 错误率 < 1%
export function thresholds(p95Ms, errorRate = 0.01) {
  return {
    http_req_duration: [`p(95)<${p95Ms}`],
    http_req_failed: [`rate<${errorRate}`],
  };
}
