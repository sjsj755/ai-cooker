/**
 * 推荐主页逻辑：持有 chips / 联想 / 忌口选中等业务状态（唯一事实来源），
 * ui.js 仅做无状态渲染。任务类型 registry：{tags, autocomplete, recommend, feedback}。
 */
"use strict";

(() => {
  const registry = createTaskRegistry();

  const MAX_INGREDIENTS = 30;
  const MAX_INGREDIENT_LEN = 50;
  const MAX_TAGS = 20;
  const DEBOUNCE_MS = 300;
  const AUTOCOMPLETE_LIMIT = 8;
  // recommend 走完整 LangGraph + 真实 LLM，冷启动/波动可达 20s+，
  // 单独放宽超时（其余任务仍用 api.js 默认 5s）。
  const RECOMMEND_TIMEOUT_MS = 30000;
  // P6.4：快响应后轮询 AI 文案补全状态（3s × 10 次 = 30s 上限）
  const AI_POLL_INTERVAL_MS = 3000;
  const AI_POLL_MAX = 10;
  const TAG_KIND_ORDER = ["过敏原", "忌口", "口味"];

  // ---- 业务状态（唯一事实来源）----
  let chips = []; // 已添加食材
  let inputValue = ""; // 输入框当前值
  let suggestions = []; // 联想结果
  let tags = []; // /api/tags 全量 [{id, name, kind}]
  let selectedTagIds = new Set(); // 忌口 / 口味选中 id
  let pending = false; // recommend 任务是否在途
  let debounceTimer = null;
  let stageTimer = null;
  let stageHint = "推荐中…";
  let aiPollTimer = null;
  let aiPollCount = 0;
  let expandedCardId = null; // 当前展开做法的菜谱 id（一次只展开一张，null 为全部收起）
  let results = []; // 最近一次推荐结果（折叠切换重渲染的数据源）
  let lastRenderedResults = null; // 上次全量渲染的 results 引用（浅比较，数据未变则增量切换）
  let feedbackByRecipe = new Map(); // recipe_id → 'like'|'dislike'（已成功提交的反馈）
  let lastFeedbackRetry = null; // {recipe, action}：反馈失败后的重试上下文

  const els = {
    chips: document.getElementById("ingredient-chips"),
    suggest: document.getElementById("ingredient-suggest"),
    tagsPicker: document.getElementById("tags-picker"),
    recommendBtn: document.getElementById("recommend-btn"),
    clearBtn: document.getElementById("clear-btn"),
    banner: document.getElementById("banner"),
    error: document.getElementById("results-error"),
    cards: document.getElementById("results-cards"),
    empty: document.getElementById("results-empty"),
    drawerRoot: document.getElementById("detail-drawer-root"),
  };

  const detail = createDetailDrawerManager({
    registry,
    drawerRoot: els.drawerRoot,
  });

  // ---- 工具 ----
  function normalizeIngredient(text) {
    return String(text || "")
      .replace(/[\x00-\x1f]/g, "")
      .trim();
  }

  function selectedTagNames() {
    const byId = new Map(tags.map((tag) => [tag.id, tag.name]));
    return [...selectedTagIds]
      .map((id) => byId.get(id))
      .filter((name) => !!name);
  }

  function tagGroups() {
    const map = new Map();
    tags.forEach((tag) => {
      if (!TAG_KIND_ORDER.includes(tag.kind)) {
        return;
      }
      if (!map.has(tag.kind)) {
        map.set(tag.kind, []);
      }
      map.get(tag.kind).push(tag);
    });
    return TAG_KIND_ORDER.filter((kind) => map.has(kind)).map((kind) => ({
      kind,
      items: map.get(kind),
    }));
  }

  function clearFormError() {
    els.error.textContent = "";
  }

  function showFormError(message) {
    UI.renderError(els.error, { type: "form", message });
  }

  function renderAll() {
    UI.renderChipInput(els.chips, {
      id: "ingredient-input",
      chips,
      value: inputValue,
      placeholder: "输入食材，回车添加",
      onAdd: addCurrentInput,
      onRemove: removeChip,
      onInput: handleInput,
      maxItems: MAX_INGREDIENTS,
      maxLen: MAX_INGREDIENT_LEN,
    });
    UI.renderSuggestions(els.suggest, suggestions, pickSuggestion);
    UI.renderTagsPicker(els.tagsPicker, {
      groups: tagGroups(),
      selected: selectedTagIds,
      onToggle: toggleTag,
    });
    els.recommendBtn.disabled = pending;
    els.recommendBtn.classList.toggle("is-loading", pending);
    els.recommendBtn.textContent = pending ? stageHint : "推荐菜谱";
    if (pending) {
      els.recommendBtn.setAttribute("aria-busy", "true");
    } else {
      els.recommendBtn.removeAttribute("aria-busy");
    }
  }

  // ---- 推荐卡片（展开状态由 expandedCardId 驱动；渲染为全量重建，ui.js 无状态） ----
  function renderCards() {
    lastRenderedResults = results;
    UI.renderCards(els.cards, results, {
      expandedId: expandedCardId,
      onToggleSteps: toggleSteps,
      onDetail: (recipe) => detail.open(recipe),
      onFeedback: submitFeedback,
      feedbackState: feedbackByRecipe,
    });
  }

  // ---- 反馈（收藏 / 不喜欢，P5）----
  function submitFeedback(recipe, action) {
    const recipeId = recipe && recipe.recipe_id;
    if (!recipeId || feedbackByRecipe.has(recipeId)) {
      return; // 已反馈过：按钮 disabled，重复点击忽略
    }
    registry
      .run("feedback", (signal) =>
        Api.requestJson("/api/feedback", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ recipe_id: recipeId, action }),
          signal,
        })
      )
      .then(() => {
        clearFormError();
        feedbackByRecipe.set(recipeId, action);
        renderCards();
      })
      .catch((err) => {
        if (err.type === "aborted") {
          return;
        }
        lastFeedbackRetry = { recipe, action };
        UI.renderError(els.error, {
          type: err.type,
          message: err.message,
          onRetry: retryFeedback,
        });
      });
  }

  function retryFeedback() {
    if (!lastFeedbackRetry) {
      return;
    }
    const { recipe, action } = lastFeedbackRetry;
    lastFeedbackRetry = null;
    submitFeedback(recipe, action);
  }

  function toggleSteps(recipe) {
    // 同卡再点收起，否则展开该卡（一次只展开一张）
    const nextId =
      expandedCardId === recipe.recipe_id ? null : recipe.recipe_id;
    expandedCardId = nextId;
    if (lastRenderedResults === results) {
      // 数据未变（浅比较：引用相等，折叠不产生新数组）：只切换 hidden / aria-expanded，
      // 避免长步骤卡片的全量重建开销；一次只展开一张、无中间态。
      els.cards.querySelectorAll(".card-toggle").forEach((btn) => {
        const id = Number(btn.getAttribute("data-toggle-id"));
        const expanded = expandedCardId === id;
        btn.setAttribute("aria-expanded", expanded ? "true" : "false");
        const wrap = document.getElementById(`steps-${id}`);
        if (wrap) {
          wrap.hidden = !expanded;
        }
      });
    } else {
      renderCards();
    }
    // 全量重建后按稳定 data-toggle-id 恢复焦点，不落到 body
    els.cards
      .querySelector(`[data-toggle-id="${recipe.recipe_id}"]`)
      ?.focus({ preventScroll: true });
  }

  // ---- 食材 chips ----
  function addCurrentInput() {
    addChip(inputValue);
  }

  function addChip(raw) {
    const name = normalizeIngredient(raw);
    if (!name) {
      return;
    }
    if (name.length > MAX_INGREDIENT_LEN) {
      showFormError(`单项食材不能超过 ${MAX_INGREDIENT_LEN} 字`);
      return;
    }
    if (chips.length >= MAX_INGREDIENTS) {
      showFormError(`最多添加 ${MAX_INGREDIENTS} 项食材`);
      return;
    }
    const lower = name.toLocaleLowerCase();
    if (chips.some((chip) => chip.toLocaleLowerCase() === lower)) {
      // 去重：不重复添加，但清空输入
      inputValue = "";
      suggestions = [];
      registry.abort("autocomplete");
      clearFormError();
      renderAll();
      return;
    }
    chips.push(name);
    inputValue = "";
    suggestions = [];
    registry.abort("autocomplete");
    clearFormError();
    renderAll();
  }

  function removeChip(name) {
    chips = chips.filter((chip) => chip !== name);
    renderAll();
  }

  // ---- 联想 ----
  function handleInput(value) {
    inputValue = value;
    clearTimeout(debounceTimer);
    if (!normalizeIngredient(value)) {
      suggestions = [];
      renderAll();
      return;
    }
    debounceTimer = setTimeout(fetchSuggestions, DEBOUNCE_MS);
    renderAll();
  }

  function fetchSuggestions() {
    const keyword = normalizeIngredient(inputValue);
    if (!keyword) {
      suggestions = [];
      renderAll();
      return;
    }
    registry
      .run("autocomplete", (signal) =>
        Api.requestJson(
          `/api/ingredients/search${Api.buildQuery({
            q: keyword,
            limit: AUTOCOMPLETE_LIMIT,
          })}`,
          { signal }
        )
      )
      .then((items) => {
        suggestions = items || [];
        renderAll();
      })
      .catch((err) => {
        if (err.type === "aborted") {
          return;
        }
        suggestions = [];
        // 联想失败不阻塞主流程：在下拉位置给出可重试的轻提示
        UI.renderError(els.suggest, {
          type: err.type,
          message: err.message,
          onRetry: fetchSuggestions,
        });
      });
  }

  function pickSuggestion(item) {
    addChip(item.name);
  }

  // ---- 忌口 / 口味 ----
  function toggleTag(id) {
    if (selectedTagIds.has(id)) {
      selectedTagIds.delete(id);
    } else {
      if (selectedTagIds.size >= MAX_TAGS) {
        showFormError(`最多选择 ${MAX_TAGS} 项忌口 / 口味`);
        return;
      }
      selectedTagIds.add(id);
    }
    renderAll();
  }

  function loadTags() {
    registry
      .run("tags", (signal) => Api.requestJson("/api/tags", { signal }))
      .then((items) => {
        tags = items || [];
        const valid = new Set(tags.map((tag) => tag.id));
        selectedTagIds = new Set(
          [...selectedTagIds].filter((id) => valid.has(id))
        );
        UI.renderTagsPicker(els.tagsPicker, {
          groups: tagGroups(),
          selected: selectedTagIds,
          onToggle: toggleTag,
        });
      })
      .catch((err) => {
        if (err.type === "aborted") {
          return;
        }
        UI.renderError(els.tagsPicker, {
          type: err.type,
          message: err.message,
          onRetry: loadTags,
        });
      });
  }

  // ---- 推荐提交 / 重试 ----
  function stopAiPoll() {
    if (aiPollTimer !== null) {
      clearTimeout(aiPollTimer);
      aiPollTimer = null;
    }
    aiPollCount = 0;
    registry.abort("recommend-status");
  }

  function startStageHints() {
    clearTimeout(stageTimer);
    stageHint = "正在识别食材…";
    stageTimer = setTimeout(() => {
      stageHint = "正在检索菜谱…";
      stageTimer = setTimeout(() => {
        stageHint = "生成 AI 文案…";
      }, 5000);
    }, 3000);
  }

  function stopStageHints() {
    clearTimeout(stageTimer);
    stageTimer = null;
    stageHint = "推荐中…";
  }

  function applyRecommendResult(payload) {
    clearFormError();
    if (payload.ai_pending) {
      // 快路径：AI 文案生成中 → 中性横幅 + 后台轮询，不走“降级提示”样式
      UI.renderBanner(els.banner, {
        degraded: false,
        notice: payload.notice || "AI 文案生成中，稍后自动更新",
      });
    } else {
      UI.renderBanner(els.banner, {
        degraded: !!payload.degraded,
        notice: payload.notice,
      });
    }
    results = payload.recipes || [];
    renderCards();
    if (results.length === 0) {
      UI.renderEmpty(
        els.empty,
        payload.notice || "未找到匹配菜谱，试试补充食材或放宽忌口"
      );
    }
  }

  function pollAiStatus() {
    aiPollCount += 1;
    registry
      .run(
        "recommend-status",
        (signal) =>
          Api.requestJson("/api/recipes/recommend/status", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              ingredients: chips,
              exclude_tags: selectedTagNames(),
            }),
            signal,
          }),
        RECOMMEND_TIMEOUT_MS
      )
      .then((status) => {
        if (status && status.ready && status.result) {
          stopAiPoll();
          applyRecommendResult(status.result);
          return;
        }
        if (status && !status.warming) {
          // 后台补全失败/已过期：停止轮询，保留快结果，可手动重新推荐
          stopAiPoll();
          UI.renderBanner(els.banner, {
            degraded: false,
            notice: "AI 文案暂不可用，可重新推荐",
          });
          return;
        }
        if (aiPollCount >= AI_POLL_MAX) {
          stopAiPoll();
          return;
        }
        aiPollTimer = setTimeout(pollAiStatus, AI_POLL_INTERVAL_MS);
      })
      .catch((err) => {
        if (err.type === "aborted") {
          return;
        }
        // 轮询自身出错不停止，继续等待（仍受 AI_POLL_MAX 上限约束）
        if (aiPollCount >= AI_POLL_MAX) {
          stopAiPoll();
          return;
        }
        aiPollTimer = setTimeout(pollAiStatus, AI_POLL_INTERVAL_MS);
      });
  }

  function startAiPoll() {
    stopAiPoll();
    pollAiStatus();
  }

  function submit() {
    if (pending) {
      return;
    }
    if (chips.length === 0) {
      showFormError("请至少添加一种食材");
      return;
    }
    stopAiPoll();
    pending = true;
    startStageHints();
    expandedCardId = null;
    results = [];
    els.cards.textContent = "";
    els.empty.textContent = "";
    els.banner.textContent = "";
    renderAll();
    registry
      .run(
        "recommend",
        (signal) =>
          Api.requestJson("/api/recipes/recommend", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              ingredients: chips,
              exclude_tags: selectedTagNames(),
            }),
            signal,
          }),
        RECOMMEND_TIMEOUT_MS
      )
      .then((payload) => {
        applyRecommendResult(payload);
        if (payload.ai_pending) {
          startAiPoll();
        }
      })
      .catch((err) => {
        if (err.type === "aborted") {
          return;
        }
        UI.renderError(els.error, {
          type: err.type,
          message: err.message,
          onRetry: retryRecommend,
        });
      })
      .finally(() => {
        pending = false;
        stopStageHints();
        renderAll();
      });
  }

  function retryRecommend() {
    registry.abort("recommend");
    stopAiPoll();
    clearFormError();
    submit();
  }

  // ---- 清空 ----
  function clearAll() {
    registry.abort("recommend");
    registry.abort("autocomplete");
    stopAiPoll();
    stopStageHints();
    chips = [];
    inputValue = "";
    suggestions = [];
    selectedTagIds = new Set();
    pending = false;
    expandedCardId = null;
    results = [];
    lastRenderedResults = null;
    feedbackByRecipe = new Map();
    lastFeedbackRetry = null;
    registry.abort("feedback");
    clearFormError();
    els.banner.textContent = "";
    els.cards.textContent = "";
    els.empty.textContent = "";
    els.suggest.textContent = "";
    detail.clear();
    renderAll();
  }

  // ---- 初始化 ----
  els.recommendBtn.addEventListener("click", submit);
  els.clearBtn.addEventListener("click", clearAll);
  renderAll();
  loadTags();
})();
