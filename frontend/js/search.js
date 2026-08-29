/**
 * 搜索页逻辑：持有自身 chips / 联想 / 忌口 / q / limit 等业务状态，
 * ui.js 仅做无状态渲染。任务类型 registry：{tags, autocomplete, search, detail}。
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
  let detailCache = new Map(); // recipe_id -> 详情数据（重复点击不重复请求）
  let openedDetailId = null; // 当前打开抽屉的菜谱 id（关闭时置空）

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
    els.searchBtn.textContent = pending ? "搜索中…" : "搜索";
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
        UI.renderCards(els.cards, payload.recipes || [], {
          onDetail: openDetail,
        });
        if (!payload.recipes || payload.recipes.length === 0) {
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

  // ---- 详情抽屉 ----
  function openDetail(recipe) {
    const recipeId = recipe.recipe_id;
    if (openedDetailId === recipeId) {
      return; // 同一卡片重复点击不重复发请求
    }
    openedDetailId = recipeId;
    registry.abort("detail"); // 切换卡片先中断在途详情请求
    if (detailCache.has(recipeId)) {
      UI.renderDetailDrawer(els.drawerRoot, detailCache.get(recipeId), {
        onClose: closeDetail,
      });
      return;
    }
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
          UI.renderDetailDrawer(els.drawerRoot, data, {
            onClose: closeDetail,
          });
        }
      })
      .catch((err) => {
        if (err.type === "aborted") {
          return;
        }
        UI.renderError(els.drawerRoot, {
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

  function closeDetail() {
    openedDetailId = null;
  }

  // ---- 清空 ----
  function clearAll() {
    registry.abort("search");
    registry.abort("autocomplete");
    registry.abort("detail");
    chips = [];
    inputValue = "";
    suggestions = [];
    selectedTagIds = new Set();
    q = "";
    pending = false;
    detailCache = new Map();
    openedDetailId = null;
    clearFormError();
    els.banner.textContent = "";
    els.cards.textContent = "";
    els.empty.textContent = "";
    els.suggest.textContent = "";
    els.drawerRoot.textContent = "";
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
