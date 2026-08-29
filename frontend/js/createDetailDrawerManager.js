/**
 * 详情抽屉管理器：封装推荐页 / 搜索页共用的抽屉状态机
 * （detailCache / openedDetailId / detailTrigger / 防重发 / 切换 abort / 焦点恢复 / 清空）。
 * 依赖全局 Api / UI；每个页面创建自己的实例并注入自己的任务 registry 与抽屉容器。
 *
 * - open(recipe)：同卡防重发 → 捕获触发元素 → abort 在途 detail →
 *   缓存命中直接渲染，否则立即渲染加载骨架（renderDrawerLoading）再异步取数；
 * - close()：置空 + 恢复触发焦点（作为抽屉 onClose 回调）；
 * - clear()：abort detail + 重置全部状态 + 清空容器（供页面“清空”调用）。
 */
"use strict";

function createDetailDrawerManager({ registry, drawerRoot }) {
  const detailCache = new Map(); // recipe_id -> 详情数据（重复点击不重复请求）
  let openedDetailId = null; // 当前打开抽屉的菜谱 id（关闭时置空）
  let detailTrigger = null; // 打开抽屉时的触发元素（关闭后恢复焦点）
  let activeDrawer = null; // 当前抽屉句柄（替换 / 关闭前解绑，防 keydown 监听累积）

  function open(recipe) {
    const recipeId = recipe.recipe_id;
    if (openedDetailId === recipeId) {
      return; // 同一卡片重复点击不重复发请求
    }
    openedDetailId = recipeId;
    detailTrigger = document.activeElement; // 捕获触发“查看详情”的元素
    registry.abort("detail"); // 切换卡片先中断在途详情请求
    if (detailCache.has(recipeId)) {
      replaceActive();
      activeDrawer = UI.renderDetailDrawer(drawerRoot, detailCache.get(recipeId), {
        onClose: close,
      });
      return;
    }
    // 立即渲染骨架 + 加载圆环，数据返回后再替换内容（消除等待焦虑）
    replaceActive();
    activeDrawer = UI.renderDrawerLoading(drawerRoot, recipe, { onClose: close });
    fetchDetail(recipeId);
  }

  function fetchDetail(recipeId) {
    registry
      .run("detail", (signal) =>
        Api.requestJson(`/api/recipes/${recipeId}`, { signal })
      )
      .then((data) => {
        detailCache.set(recipeId, data);
        // 防乱序覆盖：仅当当前打开目标仍是本次请求时才渲染
        if (openedDetailId === recipeId) {
          replaceActive();
          activeDrawer = UI.renderDetailDrawer(drawerRoot, data, { onClose: close });
        }
      })
      .catch((err) => {
        if (err.type === "aborted") {
          return;
        }
        replaceActive();
        UI.renderError(drawerRoot, {
          type: err.type,
          message: err.message,
          onRetry: () => {
            if (openedDetailId === recipeId) {
              fetchDetail(recipeId);
            }
          },
        });
      });
  }

  function replaceActive() {
    if (activeDrawer) {
      activeDrawer.destroy();
      activeDrawer = null;
    }
  }

  function close() {
    openedDetailId = null;
    replaceActive();
    // 关闭后恢复焦点到触发元素（防御清空后元素已移除的情况）
    if (detailTrigger && detailTrigger.isConnected) {
      detailTrigger.focus({ preventScroll: true });
    }
    detailTrigger = null;
  }

  function clear() {
    registry.abort("detail");
    openedDetailId = null;
    detailTrigger = null;
    replaceActive();
    detailCache.clear();
    drawerRoot.textContent = "";
    document.body.classList.remove("drawer-open");
  }

  return { open, close, clear };
}

window.createDetailDrawerManager = createDetailDrawerManager;
