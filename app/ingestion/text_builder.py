"""菜谱 → 语义单元分块：结构单元不可切，贪心合并，无字符 overlap。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.crawler import CrawledRecipe

CHUNK_SIZE = 500

UNIT_HEADER = "header"
UNIT_INGREDIENTS = "ingredients"
UNIT_STEPS = "steps"

# 句末标点 / 换行作为超长单元的回退切分边界
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；])\s*|\n+")


@dataclass
class RecipeChunk:
    """一个语义块：text 为嵌入文本，unit_type 供 P2 过滤，steps 块带起止序号。"""

    text: str
    unit_type: str
    step_start: int | None = None
    step_end: int | None = None


def build_recipe_document(recipe: CrawledRecipe) -> str:
    """标题→描述→用料（食材+调料，含用量）→步骤，段落用空行分隔（调试/整篇入文用）。"""
    lines: list[str] = [recipe.title or ""]
    if recipe.description:
        lines.append(recipe.description)
    ingredients = [
        f"{item.name}：{item.amount}" if item.amount else item.name
        for item in recipe.ingredients
    ]
    seasonings = [
        f"{item.name}：{item.amount}" if item.amount else item.name
        for item in recipe.seasonings
    ]
    if ingredients or seasonings:
        lines.append("用料：" + "；".join(ingredients + seasonings))
    for index, step in enumerate(recipe.steps, start=1):
        text = step.get("instruction") if isinstance(step, dict) else str(step)
        if text:
            lines.append(f"步骤{index}：{text}")
    return "\n\n".join(line for line in lines if line)


def _build_units(recipe: CrawledRecipe) -> list[RecipeChunk]:
    """拆成不可再分的语义单元：标题+描述 / 用料块 / 每条步骤。"""
    units: list[RecipeChunk] = []

    header_parts = [recipe.title or ""]
    if recipe.description:
        header_parts.append(recipe.description)
    header = "\n\n".join(part for part in header_parts if part)
    if header:
        units.append(RecipeChunk(text=header, unit_type=UNIT_HEADER))

    ing_lines = [
        f"{item.name}：{item.amount}" if item.amount else item.name
        for item in recipe.ingredients
    ]
    season_lines = [
        f"{item.name}：{item.amount}" if item.amount else item.name
        for item in recipe.seasonings
    ]
    if ing_lines or season_lines:
        units.append(
            RecipeChunk(
                text="用料：" + "；".join(ing_lines + season_lines),
                unit_type=UNIT_INGREDIENTS,
            )
        )

    for index, step in enumerate(recipe.steps, start=1):
        text = step.get("instruction") if isinstance(step, dict) else str(step)
        if text:
            units.append(
                RecipeChunk(
                    text=f"步骤{index}：{text}",
                    unit_type=UNIT_STEPS,
                    step_start=index,
                    step_end=index,
                )
            )
    return units


def _split_long_unit(text: str, chunk_size: int) -> list[str]:
    """超长单元回退：优先句末标点/换行切，单句仍超长才按字符硬切。"""
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]
    out: list[str] = []
    buf = ""
    for part in parts:
        if len(part) > chunk_size:
            if buf:
                out.append(buf)
                buf = ""
            out.extend(part[i : i + chunk_size] for i in range(0, len(part), chunk_size))
            continue
        if buf and len(buf) + len(part) > chunk_size:
            out.append(buf)
            buf = part
        else:
            buf = f"{buf}{part}" if buf else part
    if buf:
        out.append(buf)
    return out


def chunk_recipe(
    recipe: CrawledRecipe,
    chunk_size: int = CHUNK_SIZE,
) -> list[RecipeChunk]:
    """结构单元贪心合并：同类型单元合并到 chunk_size 上限，绝不跨类型混块。"""
    chunks: list[RecipeChunk] = []

    def append(unit: RecipeChunk) -> None:
        if (
            chunks
            and chunks[-1].unit_type == unit.unit_type
            and len(chunks[-1].text) + len(unit.text) <= chunk_size
        ):
            chunks[-1].text = f"{chunks[-1].text}\n\n{unit.text}"
            if unit.step_start is not None:
                chunks[-1].step_end = unit.step_end
            return
        chunks.append(
            RecipeChunk(
                text=unit.text,
                unit_type=unit.unit_type,
                step_start=unit.step_start,
                step_end=unit.step_end,
            )
        )

    for unit in _build_units(recipe):
        if len(unit.text) <= chunk_size:
            append(unit)
        else:
            for piece in _split_long_unit(unit.text, chunk_size):
                append(
                    RecipeChunk(
                        text=piece,
                        unit_type=unit.unit_type,
                        step_start=unit.step_start,
                        step_end=unit.step_end,
                    )
                )
    return chunks
