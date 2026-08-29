// 食材联想压测：GET /api/ingredients/search，P95 < 100ms、错误率 < 1%
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

const QUERIES = ["土", "番", "鸡", "牛", "豆"];

export default function () {
  const q = QUERIES[__ITER % QUERIES.length];
  const res = http.get(
    `${BASE_URL}/api/ingredients/search?q=${encodeURIComponent(q)}&limit=8`
  );
  check(res, { "ingredients 200": (r) => r.status === 200 });
  sleep(0.05);
}
