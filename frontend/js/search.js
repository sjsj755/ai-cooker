/**
 * 搜索页逻辑：持有自身 chips / 联想 / 忌口 / q / limit 等业务状态，
 * ui.js 仅做无状态渲染。任务类型 registry：{tags, autocomplete, search, detail, feedback}。
 */
"use strict";

(() => {
  const registry = createTaskRegistry();

  const MAX_INGREDIENTS = 30;
  const MAX_INGREDIENT_LEN = 50;
  const MAX_TAGS = 20;
  const DEBOUNCE_MS = 300;
  const AUTOCOMPLETE_LIMIT = 8;
  const TAG_KIND_ORDER = ["过敏原", "忌口", "口味"];

  // ---- 业务状态（唯一事实来源）----
  let chips = [];
  let inputValue = "";
  let suggestions = [];
  let tags = [];
  let selectedTagIds = new Set();
  let q = "";
  let limit = 10;
  let pending = false;
  let debounceTimer = null;
  let results = []; // 最近一次检索结果（反馈成功后重渲染的数据源）
  let feedbackByRecipe = new Map(); // recipe_id → 'like'|'dislike'（已成功提交的反馈）
  let lastFeedbackRetry = null; // {recipe, action}：反馈失败后的重试上下文

  const els = {
    chips: document.getElementById("search-ingredient-chips"),
    suggest: document.getElementById("search-ingredient-suggest"),
    tagsPicker: document.getElementById("search-tags-picker"),
    qInput: document.getElementById("search-q"),
    limitSelect: document.getElementById("search-limit"),
    searchBtn: document.getElementById("search-btn"),
    clearBtn: document.getElementById("search-clear-btn"),
    banner: document.getElementById("search-banner"),
    error: document.getElementById("search-results-error"),
    cards: document.getElementById("search-results-cards"),
    empty: document.getElementById("search-results-empty"),
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
      id: "search-ingredient-input",
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
    els.searchBtn.disabled = pending;
    els.searchBtn.classList.toggle("is-loading", pending);
    els.searchBtn.textContent = pending ? "搜索中…" : "搜索";
    if (pending) {
      els.searchBtn.setAttribute("aria-busy", "true");
    } else {
      els.searchBtn.removeAttribute("aria-busy");
    }
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

  // ---- 检索提交 / 重试 ----
  function submitSearch() {
    if (pending) {
      return;
    }
    const query = normalizeIngredient(q);
    if (!query) {
      showFormError("请输入搜索关键词");
      return;
    }
    pending = true;
    els.cards.textContent = "";
    els.empty.textContent = "";
    els.banner.textContent = "";
    renderAll();
    registry
      .run("search", (signal) =>
        Api.requestJson(
          `/api/recipes/search${Api.buildQuery({
            q: query,
            ingredients: chips.join(","),
            exclude_tags: selectedTagNames().join(","),
            limit,
          })}`,
          { signal }
        )
      )
      .then((payload) => {
        clearFormError();
        UI.renderBanner(els.banner, {
          degraded: !!payload.degraded,
          notice: payload.notice,
        });
        results = payload.recipes || [];
        UI.renderCards(els.cards, results, {
          onDetail: (recipe) => detail.open(recipe),
          onFeedback: submitFeedback,
          feedbackState: feedbackByRecipe,
        });
        if (results.length === 0) {
          UI.renderEmpty(
            els.empty,
            payload.notice || "未找到匹配菜谱，试试调整关键词或放宽条件"
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
          onRetry: retrySearch,
        });
      })
      .finally(() => {
        pending = false;
        renderAll();
      });
  }

  function retrySearch() {
    registry.abort("search");
    clearFormError();
    submitSearch();
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
        UI.renderCards(els.cards, results || [], {
          onDetail: (recipe) => detail.open(recipe),
          onFeedback: submitFeedback,
          feedbackState: feedbackByRecipe,
        });
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

  // ---- 清空 ----
  function clearAll() {
    registry.abort("search");
    registry.abort("autocomplete");
    chips = [];
    inputValue = "";
    suggestions = [];
    selectedTagIds = new Set();
    q = "";
    pending = false;
    results = [];
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
  els.qInput.addEventListener("input", (e) => {
    q = e.target.value;
  });
  els.qInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submitSearch();
    }
  });
  els.limitSelect.addEventListener("change", (e) => {
    limit = Number(e.target.value);
  });
  els.searchBtn.addEventListener("click", submitSearch);
  els.clearBtn.addEventListener("click", clearAll);
  renderAll();
  loadTags();
})();
