// 限流场景（P5 §3.1）：RATE_LIMIT_ENABLED=true 且 feedback 桶 20/min 时，
// 30 次连发至少 10 次 429（独立桶 + 友好 JSON）。
import http from "k6/http";
import { check } from "k6";
import { Counter } from "k6/metrics";
import { BASE_URL } from "./common.js";

const rateLimited = new Counter("rate_limited_429");

export const options = {
  scenarios: {
    burst: {
      executor: "shared-iterations",
      vus: 1,
      iterations: 30,
      maxDuration: "30s",
    },
  },
  thresholds: {
    rate_limited_429: ["count>=10"],
  },
};

export default function () {
  const res = http.post(
    `${BASE_URL}/api/feedback`,
    JSON.stringify({ recipe_id: 1, action: "like" }),
    { headers: { "Content-Type": "application/json" } }
  );
  check(res, { "200 or 429": (r) => r.status === 200 || r.status === 429 });
  check(res, {
    "friendly JSON on 429": (r) =>
      r.status !== 429 ||
      (r.json() && typeof r.json().detail === "string"),
  });
  if (res.status === 429) {
    rateLimited.add(1);
  }
}
