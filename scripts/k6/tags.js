// 标签压测：GET /api/tags，P95 < 100ms、错误率 < 1%
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

export default function () {
  const res = http.get(`${BASE_URL}/api/tags`);
  check(res, { "tags 200": (r) => r.status === 200 });
  sleep(0.05);
}
