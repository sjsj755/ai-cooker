// 推荐压测（mock LLM，确定性）：POST /api/recipes/recommend，P95 < 5s、错误率 < 1%。
// 前置：服务以 LLM_MOCK=true 启动（CI 保证确定性；真实 LLM 仅记基线见 recommend_real.js）。
import http from "k6/http";
import { check, sleep } from "k6";
import { BASE_URL, thresholds } from "./common.js";

export const options = {
  scenarios: {
    load: {
      executor: "constant-vus",
      vus: 3,
      duration: "30s",
    },
  },
  thresholds: thresholds(5000),
};

const BODIES = [
  { ingredients: ["土豆", "鸡蛋"], exclude_tags: [] },
  { ingredients: ["番茄", "牛肉"], exclude_tags: [] },
  { ingredients: ["豆腐", "青椒"], exclude_tags: ["辣"] },
];

export default function () {
  const res = http.post(
    `${BASE_URL}/api/recipes/recommend`,
    JSON.stringify(BODIES[__ITER % BODIES.length]),
    { headers: { "Content-Type": "application/json" } }
  );
  check(res, { "recommend 200": (r) => r.status === 200 });
  sleep(0.2);
}
