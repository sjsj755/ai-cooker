/**
 * 推荐主页逻辑：持有 chips / 联想 / 忌口选中等业务状态（唯一事实来源），
 * ui.js 仅做无状态渲染。任务类型 registry：{tags, autocomplete, recommend}。
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
  const TAG_KIND_ORDER = ["过敏原", "忌口", "口味"];

  // ---- 业务状态（唯一事实来源）----
  let chips = []; // 已添加食材
  let inputValue = ""; // 输入框当前值
  let suggestions = []; // 联想结果
  let tags = []; // /api/tags 全量 [{id, name, kind}]
  let selectedTagIds = new Set(); // 忌口 / 口味选中 id
  let pending = false; // recommend 任务是否在途
  let debounceTimer = null;

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
  };

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
    els.recommendBtn.textContent = pending ? "推荐中…" : "推荐菜谱";
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
  function submit() {
    if (pending) {
      return;
    }
    if (chips.length === 0) {
      showFormError("请至少添加一种食材");
      return;
    }
    pending = true;
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
        clearFormError();
        UI.renderBanner(els.banner, {
          degraded: !!payload.degraded,
          notice: payload.notice,
        });
        UI.renderCards(els.cards, payload.recipes || []);
        if (!payload.recipes || payload.recipes.length === 0) {
          UI.renderEmpty(
            els.empty,
            payload.notice || "未找到匹配菜谱，试试补充食材或放宽忌口"
          );
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
        renderAll();
      });
  }

  function retryRecommend() {
    registry.abort("recommend");
    clearFormError();
    submit();
  }

  // ---- 清空 ----
  function clearAll() {
    registry.abort("recommend");
    registry.abort("autocomplete");
    chips = [];
    inputValue = "";
    suggestions = [];
    selectedTagIds = new Set();
    pending = false;
    clearFormError();
    els.banner.textContent = "";
    els.cards.textContent = "";
    els.empty.textContent = "";
    els.suggest.textContent = "";
    renderAll();
  }

  // ---- 初始化 ----
  els.recommendBtn.addEventListener("click", submit);
  els.clearBtn.addEventListener("click", clearAll);
  renderAll();
  loadTags();
})();
