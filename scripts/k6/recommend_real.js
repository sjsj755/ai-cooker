// 真实 LLM 推荐基线（可选，仅记录；上限对齐前端 30s 超时，不设硬门禁）。
// 前置：服务以真实 LLM_API_KEY 启动。
import http from "k6/http";
import { check, sleep } from "k6";
import { BASE_URL } from "./common.js";

export const options = {
  scenarios: {
    baseline: {
      executor: "constant-vus",
      vus: 1,
      duration: "30s",
    },
  },
  thresholds: { http_req_failed: ["rate<0.05"] },
};

export default function () {
  const res = http.post(
    `${BASE_URL}/api/recipes/recommend`,
    JSON.stringify({ ingredients: ["土豆", "鸡蛋"], exclude_tags: [] }),
    { headers: { "Content-Type": "application/json" } }
  );
  check(res, { "recommend real 200": (r) => r.status === 200 });
  sleep(1);
}
