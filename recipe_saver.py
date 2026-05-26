"""
🍳 レシピ帳 — URLからNotionに自動保存
対応: クックパッド / Kurashiru / デリッシュキッチン / Allrecipes / YouTube 他
"""

import streamlit as st
import requests
from bs4 import BeautifulSoup
import json
import re
from urllib.parse import urlparse

# ─────────────────────────────────────────────
# 設定読み込み（Streamlit Cloud の Secrets から）
# ─────────────────────────────────────────────
NOTION_TOKEN = st.secrets.get("NOTION_TOKEN", "")
NOTION_DB_ID = st.secrets.get("NOTION_DB_ID", "4d144a7a9484495abb8938cf193d7f5e")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ─────────────────────────────────────────────
# ページ取得
# ─────────────────────────────────────────────
def fetch_page(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except Exception as e:
        st.warning(f"ページ取得エラー: {e}")
        return None


# ─────────────────────────────────────────────
# schema.org/Recipe 抽出
# ─────────────────────────────────────────────
def extract_schema_recipe(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, dict) and "@graph" in data:
            for item in data["@graph"]:
                if _is_recipe(item):
                    return _parse_schema(item)
        if _is_recipe(data):
            return _parse_schema(data)
        if isinstance(data, list):
            for item in data:
                if _is_recipe(item):
                    return _parse_schema(item)
    return None


def _is_recipe(data) -> bool:
    if not isinstance(data, dict):
        return False
    t = data.get("@type", "")
    return "Recipe" in (t if isinstance(t, list) else [t])


def _fmt_time(t) -> str:
    if not t:
        return ""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", str(t))
    if m:
        h, mn = m.group(1), m.group(2)
        return (f"{h}時間" if h else "") + (f"{mn}分" if mn else "")
    return str(t)


def _parse_schema(data: dict) -> dict:
    img = data.get("image", "")
    if isinstance(img, list) and img:
        img = img[0]
    if isinstance(img, dict):
        img = img.get("url", "")
    image_url = img if isinstance(img, str) else ""

    raw_inst = data.get("recipeInstructions", [])
    steps = []
    if isinstance(raw_inst, list):
        for inst in raw_inst:
            if isinstance(inst, str):
                steps.append(inst.strip())
            elif isinstance(inst, dict):
                text = inst.get("text") or inst.get("name") or ""
                if text.strip():
                    steps.append(text.strip())
    elif isinstance(raw_inst, str):
        steps = [s.strip() for s in raw_inst.split("\n") if s.strip()]

    nutrition = data.get("nutrition") or {}
    calories = nutrition.get("calories", "") if isinstance(nutrition, dict) else ""

    return {
        "title": data.get("name", "").strip(),
        "description": data.get("description", "").strip(),
        "servings": str(data.get("recipeYield", "") or ""),
        "total_time": _fmt_time(data.get("totalTime")),
        "calories": calories,
        "image_url": image_url,
        "ingredients": data.get("recipeIngredient", []) or [],
        "instructions": steps,
        "source_tag": "その他",
    }


# ─────────────────────────────────────────────
# YouTube 抽出
# ─────────────────────────────────────────────
def _yt_id(url: str) -> str:
    for pat in [r"(?:v=|\/)([0-9A-Za-z_-]{11})", r"youtu\.be\/([0-9A-Za-z_-]{11})"]:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return ""


def _text_to_ingredients(text: str) -> list[str]:
    lines, result, active = text.split("\n"), [], False
    for line in lines:
        line = line.strip()
        if re.search(r"材料|ingredient", line, re.I):
            active = True
            continue
        if re.search(r"作り方|手順|direction|instruction|step", line, re.I):
            active = False
        if active and line and re.search(r"\d|g|ml|個|本|枚|大さじ|小さじ|カップ|tbsp|tsp|cup", line):
            result.append(line)
        if len(result) >= 25:
            break
    return result


def _text_to_steps(text: str) -> list[str]:
    lines, result, active = text.split("\n"), [], False
    for line in lines:
        line = line.strip()
        if re.search(r"作り方|手順|direction|instruction|step", line, re.I):
            active = True
            continue
        if active and line:
            cleaned = re.sub(r"^[①-⑳\d][\.\)）\s]+", "", line).strip()
            if cleaned:
                result.append(cleaned)
        if len(result) >= 15:
            break
    return result


def extract_youtube(url: str) -> dict:
    vid = _yt_id(url)
    thumb = f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg" if vid else ""

    try:
        import yt_dlp  # type: ignore
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            desc = info.get("description") or ""
            return {
                "title": info.get("title", "YouTube動画"),
                "description": desc[:200],
                "image_url": info.get("thumbnail") or thumb,
                "ingredients": _text_to_ingredients(desc),
                "instructions": _text_to_steps(desc),
                "servings": "", "calories": "", "total_time": "",
                "source_tag": "YouTube",
            }
    except Exception:
        pass

    # フォールバック
    html = fetch_page(url)
    title = "YouTube動画"
    if html:
        soup = BeautifulSoup(html, "html.parser")
        t = soup.find("title")
        if t:
            title = t.get_text().replace(" - YouTube", "").strip()
    return {
        "title": title,
        "description": "概要欄からレシピを手動で追記してください。",
        "image_url": thumb,
        "ingredients": [], "instructions": [],
        "servings": "", "calories": "", "total_time": "",
        "source_tag": "YouTube",
    }


# ─────────────────────────────────────────────
# 汎用HTMLフォールバック
# ─────────────────────────────────────────────
def extract_fallback(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    parsed = urlparse(url)

    title = ""
    for sel in ["h1", ".recipe-title", '[class*="recipe"][class*="title"]']:
        el = soup.select_one(sel)
        if el:
            title = el.get_text(strip=True)
            break
    if not title:
        t = soup.find("title")
        title = t.get_text(strip=True) if t else "レシピ"

    image_url = ""
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src or src.startswith("data:"):
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = f"{parsed.scheme}://{parsed.netloc}{src}"
        if src.startswith("http") and len(src) < 600:
            alt = (img.get("alt") or "").lower()
            cls = " ".join(img.get("class") or []).lower()
            if any(k in alt + cls + src for k in ["recipe", "food", "dish", "cook", "main"]):
                image_url = src
                break

    return {
        "title": title, "description": "",
        "image_url": image_url,
        "ingredients": [], "instructions": [],
        "servings": "", "calories": "", "total_time": "",
        "source_tag": "その他",
    }


# ─────────────────────────────────────────────
# サイト判定でソースタグを付与
# ─────────────────────────────────────────────
def detect_source_tag(url: str) -> str:
    u = url.lower()
    if "cookpad.com" in u:        return "クックパッド"
    if "kurashiru.com" in u:      return "Kurashiru"
    if "delishkitchen.tv" in u:   return "デリッシュキッチン"
    if "allrecipes.com" in u:     return "Allrecipes"
    if "youtube.com" in u or "youtu.be" in u: return "YouTube"
    return "その他"


# ─────────────────────────────────────────────
# Notion 保存
# ─────────────────────────────────────────────
def save_to_notion(recipe: dict, source_url: str) -> str:
    """Notionにレシピページを作成してURLを返す"""
    from notion_client import Client  # type: ignore

    notion = Client(auth=NOTION_TOKEN)
    source_tag = detect_source_tag(source_url)

    # プロパティ
    properties = {
        "レシピ名": {"title": [{"text": {"content": recipe["title"][:100]}}]},
        "ソース": {"select": {"name": source_tag}},
        "URL": {"url": source_url},
    }
    if recipe.get("servings"):
        properties["人数"] = {"rich_text": [{"text": {"content": recipe["servings"][:50]}}]}
    if recipe.get("total_time"):
        properties["調理時間"] = {"rich_text": [{"text": {"content": recipe["total_time"][:50]}}]}
    if recipe.get("calories"):
        properties["カロリー"] = {"rich_text": [{"text": {"content": str(recipe["calories"])[:50]}}]}

    # ページ本文ブロック
    children = []

    if recipe.get("description"):
        children.append({
            "object": "block", "type": "quote",
            "quote": {"rich_text": [{"text": {"content": recipe["description"][:1000]}}]},
        })
        children.append({"object": "block", "type": "divider", "divider": {}})

    if recipe.get("ingredients"):
        children.append({
            "object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"text": {"content": "🥕 材料"}}]},
        })
        for ing in recipe["ingredients"][:50]:
            children.append({
                "object": "block", "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"text": {"content": str(ing)[:200]}}]},
            })

    if recipe.get("instructions"):
        children.append({"object": "block", "type": "divider", "divider": {}})
        children.append({
            "object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"text": {"content": "👨‍🍳 作り方"}}]},
        })
        for step in recipe["instructions"][:30]:
            children.append({
                "object": "block", "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": [{"text": {"content": str(step)[:2000]}}]},
            })

    # カバー画像
    cover = None
    if recipe.get("image_url"):
        cover = {"type": "external", "external": {"url": recipe["image_url"]}}

    page = notion.pages.create(
        parent={"database_id": NOTION_DB_ID},
        cover=cover,
        properties=properties,
        children=children,
    )
    return page["url"]


# ─────────────────────────────────────────────
# Streamlit UI
# ─────────────────────────────────────────────
st.set_page_config(page_title="🍳 レシピ帳", page_icon="🍳", layout="wide")

st.title("🍳 レシピ帳")
st.caption("URLを貼るだけで材料・作り方をNotionに自動保存")

# Notionトークン未設定の場合は警告
if not NOTION_TOKEN:
    st.error(
        "⚠️ Notionトークンが設定されていません。\n\n"
        "Streamlit Cloud の Settings → Secrets に以下を追加してください：\n\n"
        "```\nNOTION_TOKEN = \"secret_xxxx\"\n```"
    )
    st.stop()

# ─── URL入力 ───
url_input = st.text_input(
    "📎 レシピURLを貼り付け",
    placeholder="https://cookpad.com/recipe/...  /  https://www.youtube.com/watch?v=...",
)
extract_btn = st.button("🔍 レシピを読み込む", type="primary", use_container_width=False)

# ─── 抽出 ───
if extract_btn and url_input:
    if "instagram.com" in url_input:
        st.error("Instagramはログイン必須のため自動抽出できません。")
    else:
        with st.spinner("読み込み中..."):
            is_yt = "youtube.com" in url_input or "youtu.be" in url_input
            recipe = None

            if is_yt:
                recipe = extract_youtube(url_input)
            else:
                html = fetch_page(url_input)
                if html:
                    recipe = extract_schema_recipe(html)
                    if not recipe:
                        st.info("構造化データなし → HTML汎用解析で試みます")
                        recipe = extract_fallback(html, url_input)

            if recipe:
                st.session_state["recipe"] = recipe
                st.session_state["url"] = url_input

# ─── プレビュー & 保存 ───
if "recipe" in st.session_state:
    recipe = st.session_state["recipe"]
    source_url = st.session_state.get("url", "")

    st.divider()
    st.subheader("📋 プレビュー")

    col_l, col_r = st.columns([3, 2])
    with col_l:
        recipe["title"] = st.text_input("レシピ名（編集可）", value=recipe.get("title", ""))

        if recipe.get("description"):
            st.caption(recipe["description"][:200])

        m1, m2, m3 = st.columns(3)
        if recipe.get("servings"):    m1.metric("人数", recipe["servings"])
        if recipe.get("total_time"): m2.metric("調理時間", recipe["total_time"])
        if recipe.get("calories"):   m3.metric("カロリー", recipe["calories"])

        st.markdown("##### 🥕 材料")
        if recipe.get("ingredients"):
            for ing in recipe["ingredients"]:
                st.markdown(f"- {ing}")
        else:
            st.caption("材料を自動抽出できませんでした")

        st.markdown("##### 👨‍🍳 作り方")
        if recipe.get("instructions"):
            for i, step in enumerate(recipe["instructions"], 1):
                st.markdown(f"**{i}.** {step}")
        else:
            st.caption("作り方を自動抽出できませんでした")

    with col_r:
        if recipe.get("image_url"):
            try:
                st.image(recipe["image_url"], use_container_width=True)
            except Exception:
                st.caption("画像プレビュー不可")

    st.divider()
    col_save, col_clear = st.columns([2, 1])
    with col_save:
        if st.button("📝 Notionに保存する", type="primary", use_container_width=True):
            with st.spinner("Notionに保存中..."):
                try:
                    page_url = save_to_notion(recipe, source_url)
                    st.success("✅ Notionに保存しました！")
                    st.markdown(f"[📖 Notionで開く]({page_url})")
                    st.balloons()
                except Exception as e:
                    st.error(f"保存エラー: {e}")
    with col_clear:
        if st.button("🗑️ クリア", use_container_width=True):
            del st.session_state["recipe"]
            st.rerun()
