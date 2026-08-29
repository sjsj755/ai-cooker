// 反馈压测：POST /api/feedback，P95 < 200ms、错误率 < 1%。
// 前置：RATE_LIMIT_ENABLED=false（限流效果用 rate_limit.js 单独验证）。
// setup() 从 search 发现合法 id 池（避免 404 污染错误率；同 id 幂等 200 不膨胀表）。
import http from "k6/http";
import { check, sleep } from "k6";
import { BASE_URL, thresholds } from "./common.js";

export const options = {
  scenarios: {
    load: {
      executor: "constant-vus",
      vus: 10,
      duration: "30s",
    },
  },
  thresholds: thresholds(200),
};

export function setup() {
  const res = http.get(
    `${BASE_URL}/api/recipes/search?q=${encodeURIComponent("土豆")}&limit=50`
  );
  const pool = (res.json().recipes || []).map((r) => r.recipe_id);
  return pool.length ? pool : [1];
}

export default function (data) {
  const recipeId = data[__ITER % data.length];
  const action = __ITER % 2 === 0 ? "like" : "dislike";
  const res = http.post(
    `${BASE_URL}/api/feedback`,
    JSON.stringify({ recipe_id: recipeId, action }),
    { headers: { "Content-Type": "application/json" } }
  );
  check(res, { "feedback 200": (r) => r.status === 200 });
  sleep(0.05);
}
