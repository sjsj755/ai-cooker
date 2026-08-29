// 详情压测：GET /api/recipes/{id}，P95 < 100ms、错误率 < 1%
// setup() 从 search 接口发现合法 id 池（dev 库 id 存在空洞，避免 404 污染错误率）。
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
  thresholds: thresholds(100),
};

export function setup() {
  const res = http.get(
    `${BASE_URL}/api/recipes/search?q=${encodeURIComponent("土豆")}&limit=50`
  );
  const pool = (res.json().recipes || []).map((r) => r.recipe_id);
  return pool.length ? pool : [1];
}

export default function (data) {
  const id = data[__ITER % data.length];
  const res = http.get(`${BASE_URL}/api/recipes/${id}`);
  check(res, { "detail status 200": (r) => r.status === 200 });
  sleep(0.05);
}
