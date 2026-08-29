// 检索规模基线：RECIPES_SCALE=10k 硬门禁 P95<200ms；50k 档仅留痕（无硬阈值，
// 必须记录数值 + 硬件配置 + summary JSON/截图，见 P5_PLAN §6.3）。
// 负载画像与 search.js 对齐：5 并发搜索用户 × 30s。
import http from "k6/http";
import { check, sleep } from "k6";
import { BASE_URL, thresholds } from "./common.js";

const scale = __ENV.RECIPES_SCALE || "10k";

export const options = {
  scenarios: {
    load: {
      executor: "constant-vus",
      vus: 5,
      duration: "30s",
    },
  },
  // 10k：硬门禁；50k：只留痕（宽松错误率门禁防联调事故）
  thresholds:
    scale === "10k"
      ? thresholds(200)
      : { http_req_failed: ["rate<0.02"] },
};

const QUERIES = ["土豆 鸡蛋", "番茄 牛肉", "青椒 茄子", "豆腐", "虾仁 黄瓜"];

export default function () {
  const q = QUERIES[__ITER % QUERIES.length];
  const res = http.get(
    `${BASE_URL}/api/recipes/search?q=${encodeURIComponent(q)}&limit=10`
  );
  check(res, { "search scale 200": (r) => r.status === 200 });
  sleep(0.1);
}
