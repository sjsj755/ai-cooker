/**
 * 无状态视图层：不持有业务状态，只提供纯渲染函数与回调绑定。
 * 所有渲染一律 createElement + textContent（不拼接 HTML 字符串、不执行动态脚本）；
 * 外链统一 rel="noopener noreferrer"。
 */
"use strict";

const UI = (() => {
  const KIND_ORDER = ["过敏原", "忌口", "口味"];

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function clear(container) {
    container.textContent = "";
  }

  /** 难度（1-3）转星标展示。 */
  function starRating(difficulty) {
    if (difficulty === null || difficulty === undefined) {
      return "难度未知";
    }
    const n = Math.max(1, Math.min(3, Number(difficulty) || 1));
    return "★".repeat(n);
  }

  /** steps（list[dict]，instruction/step/text 兼容）渲染为有序列表。 */
  function renderSteps(steps) {
    const listEl = el("ol", "recipe-steps");
    (steps || []).forEach((step) => {
      const text =
        typeof step === "string"
          ? step
          : step && (step.instruction || step.step || step.text);
      if (text) {
        listEl.append(el("li", "", text));
      }
    });
    return listEl;
  }

  /** 缺料 chips：无缺料显示“无需额外食材”。 */
  function missingChips(list) {
    const wrap = el("div", "missing");
    wrap.append(el("span", "missing-label", "缺料："));
    if (!list || list.length === 0) {
      wrap.append(el("span", "missing-none", "无需额外食材"));
    } else {
      list.forEach((name) => wrap.append(el("span", "chip chip-missing", name)));
    }
    return wrap;
  }

  /** 所需调料行：仅名称 chips（用量只在详情抽屉展示）。 */
  function seasoningsRow(list) {
    const wrap = el("div", "seasonings");
    wrap.append(el("span", "seasonings-label", "所需调料："));
    list.forEach((item) => {
      const name = typeof item === "string" ? item : item && item.name;
      if (name) {
        wrap.append(el("span", "chip chip-seasoning", name));
      }
    });
    return wrap;
  }

  /**
   * 食材 / 标签自由文本 chips 输入组件。
   * 业务状态（chips 数组 / 输入框值）由页面脚本持有并传入；组件只负责渲染与回调绑定。
   */
  function renderChipInput(container, opts) {
    let root = container.querySelector(".chip-input");
    if (!root) {
      root = el("div", "chip-input");
      const chipsRow = el("div", "chips");
      const input = document.createElement("input");
      input.type = "text";
      input.className = "chip-input-field";
      if (opts.id) {
        input.id = opts.id;
      }
      input.setAttribute("autocomplete", "off");
      input.addEventListener("keydown", (e) => {
        const current = root._opts;
        if (e.key === "Enter") {
          e.preventDefault();
          current.onAdd();
        } else if (
          e.key === "Backspace" &&
          !input.value &&
          current.chips &&
          current.chips.length > 0
        ) {
          current.onRemove(current.chips[current.chips.length - 1]);
        }
      });
      input.addEventListener("input", () => {
        root._opts.onInput(input.value);
      });
      root.append(chipsRow, input);
      container.append(root);
      root._chipsRow = chipsRow;
      root._input = input;
    }
    root._opts = opts;
    root._input.placeholder = opts.placeholder || "";
    root._input.maxLength = opts.maxLen || 50;
    root._input.disabled =
      !!opts.maxItems && opts.chips && opts.chips.length >= opts.maxItems;
    const row = root._chipsRow;
    clear(row);
    (opts.chips || []).forEach((chip) => {
      const chipEl = el("span", "chip");
      chipEl.append(el("span", "chip-label", chip));
      const removeBtn = el("button", "chip-remove", "×");
      removeBtn.type = "button";
      removeBtn.setAttribute("aria-label", `移除 ${chip}`);
      removeBtn.addEventListener("click", () => opts.onRemove(chip));
      chipEl.append(removeBtn);
      row.append(chipEl);
    });
    if (root._input.value !== (opts.value || "")) {
      root._input.value = opts.value || "";
    }
    return root;
  }

  /** 食材联想下拉（items: [{name, category?}]），onPick(item) 点选插入。 */
  function renderSuggestions(container, items, onPick) {
    clear(container);
    if (!items || items.length === 0) {
      return;
    }
    const listEl = el("ul", "suggest-list");
    items.forEach((item) => {
      const li = document.createElement("li");
      const btn = el("button", "suggest-item", item.name);
      btn.type = "button";
      if (item.category) {
        btn.append(el("span", "suggest-meta", item.category));
      }
      btn.addEventListener("click", () => onPick(item));
      li.append(btn);
      listEl.append(li);
    });
    container.append(listEl);
  }

  /**
   * 忌口 / 口味多选：groups=[{kind, items:[{id, name}]}]，selected 为 Set(id)。
   * 仅展示 过敏原 / 忌口 / 口味，菜系不展示。
   */
  function renderTagsPicker(container, { groups, selected, onToggle }) {
    clear(container);
    groups.forEach((group) => {
      const field = el("div", "tag-group");
      field.append(el("h3", "tag-group-title", group.kind));
      const wrap = el("div", "tag-items");
      group.items.forEach((tag) => {
        const btn = el("button", "tag-item", tag.name);
        btn.type = "button";
        const active = selected.has(tag.id);
        if (active) {
          btn.classList.add("active");
        }
        btn.setAttribute("aria-pressed", active ? "true" : "false");
        btn.addEventListener("click", () => onToggle(tag.id));
        wrap.append(btn);
      });
      field.append(wrap);
      container.append(field);
    });
  }

  /**
   * 结果卡片：推荐主页内联展示缺料 / 难度 / 时长，步骤折叠（一次只展开一张）；
   * 搜索页传 options.onDetail 时附加“查看详情”按钮（不渲染步骤）。
   * 匹配度徽章按本批 match_score 最高分归一为相对百分比（RRF 融合分绝对量纲极小）。
   * options: { expandedId, onToggleSteps, onDetail }
   * - expandedId：当前展开的 recipe_id（hidden / aria-expanded 按它推导）；
   * - onToggleSteps(recipe)：点击“做法”按钮回调（页面脚本更新展开状态后全量重渲染）；
   * - 展开容器常驻 hidden，aria-controls 恒指向它；重建后由页面脚本按 data-toggle-id 恢复焦点。
   */
  function renderCards(container, recipes, options = {}) {
    clear(container);
    // 匹配度徽章：match_score 是 RRF 融合分（绝对量纲极小，上界约 0.016），
    // 直接 ×100 会全部显示 1%；按本批最高分归一为相对百分比（0-100）再展示。
    const scores = (recipes || [])
      .map((r) => r.match_score)
      .filter((s) => typeof s === "number" && Number.isFinite(s));
    const maxScore = scores.length > 0 ? Math.max(...scores) : 0;
    (recipes || []).forEach((recipe) => {
      const card = el("article", "card");
      // 头行：标题 + 匹配度徽章
      const head = el("div", "card-head");
      head.append(el("h3", "card-title", recipe.title));
      if (typeof recipe.match_score === "number") {
        const pct =
          maxScore > 0
            ? Math.max(
                0,
                Math.min(100, Math.round((recipe.match_score / maxScore) * 100))
              )
            : 0;
        head.append(
          el("span", "badge", `${pct}% 匹配`)
        );
      }
      card.append(head);
      // 弱化元信息行：难度 / 时长（未知省略）
      const meta = el("div", "card-meta");
      if (recipe.difficulty !== undefined && recipe.difficulty !== null) {
        meta.append(el("span", "card-line", `难度 ${starRating(recipe.difficulty)}`));
      }
      if (recipe.cook_time_minutes !== undefined && recipe.cook_time_minutes !== null) {
        meta.append(el("span", "card-line", `约 ${recipe.cook_time_minutes} 分钟`));
      }
      if (meta.childNodes.length > 0) {
        card.append(meta);
      }
      card.append(missingChips(recipe.missing_ingredients));
      // 所需调料行（空则不渲染；用量只在详情抽屉展示）
      if (Array.isArray(recipe.seasonings) && recipe.seasonings.length > 0) {
        card.append(seasoningsRow(recipe.seasonings));
      }
      // 操作行：做法（折叠）+ 查看详情（抽屉）
      const actions = el("div", "card-actions");
      let stepsWrap = null;
      if (typeof options.onToggleSteps === "function") {
        stepsWrap = el("div", "recipe-steps-wrap");
        stepsWrap.id = `steps-${recipe.recipe_id}`;
        const expanded = options.expandedId === recipe.recipe_id;
        stepsWrap.hidden = !expanded;
        if (Array.isArray(recipe.steps) && recipe.steps.length > 0) {
          stepsWrap.append(renderSteps(recipe.steps));
        }
        if (recipe.tips) {
          stepsWrap.append(el("p", "card-tips", `小贴士：${recipe.tips}`));
        }
        const toggle = el("button", "btn card-toggle", "做法");
        toggle.type = "button";
        toggle.setAttribute("data-toggle-id", String(recipe.recipe_id));
        toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
        toggle.setAttribute("aria-controls", stepsWrap.id);
        toggle.addEventListener("click", () => options.onToggleSteps(recipe));
        actions.append(toggle);
      }
      if (typeof options.onDetail === "function") {
        const detailBtn = el("button", "btn ghost", "查看详情");
        detailBtn.type = "button";
        detailBtn.addEventListener("click", () => options.onDetail(recipe));
        actions.append(detailBtn);
      }
      if (actions.childNodes.length > 0) {
        card.append(actions);
      }
      if (stepsWrap) {
        card.append(stepsWrap);
      }
      container.append(card);
    });
  }

  /**
   * 抽屉挂载助手：overlay / drawer / head / 关闭按钮 / 滚动锁定 / ESC / 遮罩 / 聚焦。
   * 返回 { body, close, destroy }；destroy 幂等（解绑 keydown + 清空 + 解锁），
   * 供替换内容（加载 → 数据）与关闭复用，杜绝监听累积。
   */
  function _mountDrawer(container, recipe, onClose) {
    clear(container);
    const overlay = el("div", "drawer-overlay");
    const drawer = el("aside", "drawer");
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-modal", "true");
    drawer.setAttribute("aria-label", recipe.title || "菜谱详情");

    const head = el("div", "drawer-head");
    head.append(el("h2", "drawer-title", recipe.title || "菜谱详情"));
    const closeBtn = el("button", "drawer-close", "×");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "关闭详情");
    head.append(closeBtn);

    const body = el("div", "drawer-body");
    drawer.append(head, body);
    overlay.append(drawer);
    container.append(overlay);

    // 打开时锁定页面滚动
    document.body.classList.add("drawer-open");

    function destroy() {
      document.body.classList.remove("drawer-open");
      clear(container);
      document.removeEventListener("keydown", onKey);
    }
    function close() {
      destroy();
      if (typeof onClose === "function") {
        onClose();
      }
    }
    function onKey(e) {
      if (e.key === "Escape") {
        close();
      }
    }
    closeBtn.addEventListener("click", close);
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) {
        close();
      }
    });
    document.addEventListener("keydown", onKey);
    // 打开时聚焦关闭按钮（可随时 ESC / 点击遮罩关闭）
    closeBtn.focus({ preventScroll: true });
    return { body, close, destroy };
  }

  /** 用料列表（食材 / 调料）：名称 + 可选用量。 */
  function renderIngredientList(list, className) {
    const ulEl = el("ul", className);
    (list || []).forEach((item) => {
      const name = typeof item === "string" ? item : item && item.name;
      const amount = item && item.amount;
      if (!name) {
        return;
      }
      const li = el("li", "");
      li.append(document.createTextNode(name));
      if (amount) {
        li.append(el("span", "drawer-amount", ` ${amount}`));
      }
      ulEl.append(li);
    });
    return ulEl;
  }

  /** 详情抽屉：食材 / 调料 / 描述 / 难度 / 时长 / 份数 / 步骤 / 来源外链；onClose 在关闭时回调。 */
  function renderDetailDrawer(container, recipe, options = {}) {
    const { body, destroy } = _mountDrawer(container, recipe, options.onClose);
    if (recipe.description) {
      body.append(el("p", "drawer-desc", recipe.description));
    }
    const meta = el("ul", "drawer-meta");
    if (recipe.difficulty !== undefined && recipe.difficulty !== null) {
      meta.append(el("li", "", `难度：${starRating(recipe.difficulty)}`));
    }
    if (recipe.cook_time_minutes !== undefined && recipe.cook_time_minutes !== null) {
      meta.append(el("li", "", `时长：约 ${recipe.cook_time_minutes} 分钟`));
    }
    if (recipe.servings !== undefined && recipe.servings !== null) {
      meta.append(el("li", "", `份数：${recipe.servings} 人份`));
    }
    body.append(meta);
    // 所需食材 / 调料（空则不渲染）
    if (Array.isArray(recipe.ingredients) && recipe.ingredients.length > 0) {
      body.append(el("h3", "drawer-subtitle", "所需食材"));
      body.append(renderIngredientList(recipe.ingredients, "drawer-ingredients"));
    }
    if (Array.isArray(recipe.seasonings) && recipe.seasonings.length > 0) {
      body.append(el("h3", "drawer-subtitle", "调料"));
      body.append(renderIngredientList(recipe.seasonings, "drawer-seasonings"));
    }
    if (Array.isArray(recipe.steps) && recipe.steps.length > 0) {
      body.append(el("h3", "drawer-subtitle", "做法步骤"));
      body.append(renderSteps(recipe.steps));
    } else {
      body.append(el("p", "drawer-empty", "暂无步骤数据"));
    }
    if (recipe.source_url) {
      const link = el("a", "drawer-source", "查看来源");
      link.href = recipe.source_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      body.append(link);
    }
    return { destroy };
  }

  /** 详情加载骨架：抽屉外壳 + 圆环（复用 .is-loading 动画），数据返回后由管理器替换。 */
  function renderDrawerLoading(container, recipe, options = {}) {
    const { body, destroy } = _mountDrawer(container, recipe, options.onClose);
    const row = el("p", "drawer-loading");
    const spinner = el("span", "spinner");
    spinner.setAttribute("aria-hidden", "true");
    row.append(spinner, el("span", "", "正在加载详情…"));
    body.append(row);
    return { destroy };
  }

  /** 降级 / 提示横幅：degraded=true 琥珀色，否则中性提示。 */
  function renderBanner(container, { degraded, notice }) {
    clear(container);
    if (!notice && !degraded) {
      return;
    }
    const banner = el("div", degraded ? "banner banner-warn" : "banner banner-info");
    if (degraded) {
      banner.append(el("strong", "", "降级提示："));
    }
    banner.append(el("span", "", notice || ""));
    container.append(banner);
  }

  /** 错误渲染：server / network / timeout 附重试按钮；aborted 由页面脚本静默处理不调用。 */
  function renderError(container, { type, message, onRetry }) {
    clear(container);
    const box = el("div", "error-box");
    box.append(el("p", "error-text", message || "请求失败"));
    if (
      typeof onRetry === "function" &&
      (type === "server" || type === "network" || type === "timeout")
    ) {
      const btn = el("button", "btn primary", "重试");
      btn.type = "button";
      btn.addEventListener("click", onRetry);
      box.append(btn);
    }
    container.append(box);
  }

  function renderEmpty(container, message) {
    clear(container);
    container.append(el("div", "empty", message));
  }

  return {
    renderChipInput,
    renderSuggestions,
    renderTagsPicker,
    renderCards,
    renderDetailDrawer,
    renderDrawerLoading,
    renderBanner,
    renderError,
    renderEmpty,
  };
})();
