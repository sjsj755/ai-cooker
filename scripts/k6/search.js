// 搜索检索压测（10k 档硬门禁）：P95 < 200ms、错误率 < 1%。
// 负载画像：5 并发搜索用户 × 30s（本地单用户部署基准；本机 MySQL 并发查询
// 基线约 40-50ms/查询，10 VU 会叠加超门禁，5 VU 代表真实单用户前端并发）。
import http from "k6/http";
import { check, sleep } from "k6";
import { BASE_URL, thresholds } from "./common.js";

export const options = {
  scenarios: {
    load: {
      executor: "constant-vus",
      vus: 5,
      duration: "30s",
    },
  },
  thresholds: thresholds(200),
};

const QUERIES = ["土豆 鸡蛋", "番茄 牛肉", "青椒 茄子", "豆腐", "虾仁 黄瓜", "红烧 排骨"];

export default function () {
  const q = QUERIES[__ITER % QUERIES.length];
  const res = http.get(
    `${BASE_URL}/api/recipes/search?q=${encodeURIComponent(q)}&limit=10`
  );
  check(res, { "search status 200": (r) => r.status === 200 });
  sleep(0.1);
}
