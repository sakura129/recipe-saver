"""
🍳 レシピ帳 — URLまたはテキストからNotionに自動保存
対応: クックパッド / Kurashiru / デリッシュキッチン / Allrecipes / YouTube / Instagram（テキスト貼り付け）他
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
NOTION_TOKEN   = st.secrets.get("NOTION_TOKEN", "")
NOTION_DB_ID   = st.secrets.get("NOTION_DB_ID", "4d144a7a9484495abb8938cf193d7f5e")
ANTHROPIC_KEY  = st.secrets.get("ANTHROPIC_API_KEY", "")

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
# Claude API でテキストからレシピを抽出
# ─────────────────────────────────────────────
def extract_from_text_with_claude(raw_text: str, image_url: str = "") -> dict:
    """
    Instagramキャプション等の自由テキストをClaude APIで解析し
    構造化されたレシピデータを返す。
    """
    import anthropic  # type: ignore

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    prompt = f"""以下のテキストはInstagramやSNSに投稿されたレシピです。
このテキストから以下の情報をJSON形式で抽出してください。

抽出項目:
- title: レシピ名（文中から推測、なければ「レシピ」）
- servings: 何人分か（数字＋「人分」の形式。記載なければ空文字）
- total_time: 調理時間（例: "20分"。記載なければ空文字）
- calories: カロリー（例: "300kcal"。記載なければ空文字）
- ingredients: 材料のリスト（各要素は「食材名 量」の文字列。最大30個）
- instructions: 作り方のステップリスト（番号を除いた手順文。最大20ステップ）

ルール:
- 必ずJSONのみを返す（説明文は不要）
- ingredientsとinstructionsは文字列の配列で返す
- 情報がない項目は空文字または空配列にする

テキスト:
\"\"\"
{raw_text[:3000]}
\"\"\"
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    # コードブロックを除去
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    parsed = json.loads(raw)

    return {
        "title":        parsed.get("title", "レシピ") or "レシピ",
        "description":  "",
        "servings":     parsed.get("servings", ""),
        "total_time":   parsed.get("total_time", ""),
        "calories":     parsed.get("calories", ""),
        "image_url":    image_url,
        "ingredients":  parsed.get("ingredients", []),
        "instructions": parsed.get("instructions", []),
        "source_tag":   "その他",
    }


# ─────────────────────────────────────────────
# 画像を Telegraph（無料）にアップロードしてURLを返す
# ─────────────────────────────────────────────
def upload_image_telegraph(image_bytes: bytes, filename: str = "image.jpg") -> str:
    """
    Telegram の Telegraph サービスに画像をアップロードし公開URLを返す。
    API キー不要・無料・永続。
    """
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else "jpg"
    content_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
    resp = requests.post(
        "https://telegra.ph/upload",
        files={"file": (filename, image_bytes, content_type)},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list) and data:
        return "https://telegra.ph" + data[0]["src"]
    raise Exception(f"画像アップロード失敗: {data}")


# ─────────────────────────────────────────────
# サイト判定でソースタグを付与
# ─────────────────────────────────────────────
def detect_source_tag(url: str) -> str:
    u = url.lower()
    if "cookpad.com" in u:        return "クックパッド"
    if "kurashiru.com" in u:      return "Kurashiru"
    if "delishkitchen.tv" in u:   return "デリッシュキッチン"
    if "allrecipes.com" in u:     return "Allrecipes"
    if "instagram.com" in u:      return "Instagram"
    if "youtube.com" in u or "youtu.be" in u: return "YouTube"
    return "その他"


# ─────────────────────────────────────────────
# Notion 保存
# ─────────────────────────────────────────────
def save_to_notion(recipe: dict, source_url: str) -> str:
    """Notionにレシピページを作成してURLを返す"""
    from notion_client import Client  # type: ignore

    notion = Client(auth=NOTION_TOKEN)
    # recipe に source_tag が明示されていればそれを優先（Instagramテキストモード等）
    source_tag = recipe.get("source_tag") or detect_source_tag(source_url)

    # プロパティ
    properties = {
        "レシピ名": {"title": [{"text": {"content": recipe["title"][:100]}}]},
        "ソース": {"select": {"name": source_tag}},
        "URL": {"url": source_url if source_url else None},
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
st.caption("URLを貼るか、InstagramのキャプションをコピペするだけでNotionに自動保存")

# ─── Notionトークン未設定チェック ───
if not NOTION_TOKEN:
    st.error(
        "⚠️ Notionトークンが設定されていません。\n\n"
        "Streamlit Cloud の Settings → Secrets に以下を追加してください：\n\n"
        "```toml\n"
        "NOTION_TOKEN = \"ntn_xxxx\"\n"
        "NOTION_DB_ID = \"4d144a7a9484495abb8938cf193d7f5e\"\n"
        "ANTHROPIC_API_KEY = \"sk-ant-xxxx\"\n"
        "```"
    )
    st.stop()

# ─────────────────────────────────────────────
# タブ切り替え
# ─────────────────────────────────────────────
tab_url, tab_text = st.tabs(["🔗 URLから保存（レシピサイト・YouTube）", "📸 テキストから保存（Instagram・手動）"])


# ══════════════════════════════════════════════
# TAB 1 : URLモード（既存機能）
# ══════════════════════════════════════════════
with tab_url:
    url_input = st.text_input(
        "📎 レシピURLを貼り付け",
        placeholder="https://cookpad.com/recipe/...  /  https://www.youtube.com/watch?v=...",
        key="url_input",
    )
    extract_btn = st.button("🔍 レシピを読み込む", type="primary", key="btn_url")

    if extract_btn and url_input:
        if "instagram.com" in url_input:
            st.warning("Instagramは「テキストから保存」タブをご利用ください 👉")
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
                    st.session_state["source_url"] = url_input


# ══════════════════════════════════════════════
# TAB 2 : テキスト貼り付けモード（Instagram等）
# ══════════════════════════════════════════════
with tab_text:
    st.markdown("""
    **Instagramの使い方（3ステップ）**
    1. Instagramでレシピ投稿を開く
    2. キャプション（文章部分）を**長押し → 全選択 → コピー**
    3. 下のボックスに貼り付けて「解析する」を押す
    """)

    # ── Instagram URL ──
    instagram_url = st.text_input(
        "🔗 InstagramのURL（任意）",
        placeholder="https://www.instagram.com/p/xxxxx/ または reel/xxxxx/",
        key="instagram_url",
        help="Notionのプロパティに保存されます。なくてもOK。",
    )

    col_img, col_paste = st.columns([1, 2])

    with col_img:
        uploaded_file = st.file_uploader(
            "🖼️ 画像をアップロード（任意）",
            type=["jpg", "jpeg", "png", "webp"],
            key="uploaded_image",
            help="スマホのカメラロールからも選べます。Notionのカバー画像になります。",
        )
        if uploaded_file:
            st.image(uploaded_file, use_container_width=True)

    with col_paste:
        pasted_text = st.text_area(
            "📋 レシピテキストを貼り付け",
            height=220,
            placeholder="例）\n材料（2人分）\n鶏もも肉 300g\n醤油 大さじ2\n...\n\n作り方\n①鶏肉を一口大に切る\n②フライパンで焼く...",
            key="pasted_text",
        )

    parse_btn = st.button("✨ AIで解析する", type="primary", key="btn_text")

    if parse_btn:
        if not pasted_text.strip():
            st.warning("テキストを貼り付けてください。")
        elif not ANTHROPIC_KEY:
            st.error(
                "⚠️ ANTHROPIC_API_KEY が設定されていません。\n\n"
                "Streamlit Cloud の Secrets に `ANTHROPIC_API_KEY = \"sk-ant-xxxx\"` を追加してください。"
            )
        else:
            with st.spinner("Claude AIで解析中..."):
                try:
                    recipe = extract_from_text_with_claude(pasted_text)
                    recipe["source_tag"] = "Instagram"

                    # アップロード済み画像をセッションに保持
                    if uploaded_file:
                        st.session_state["uploaded_image_bytes"] = uploaded_file.read()
                        st.session_state["uploaded_image_name"] = uploaded_file.name
                    else:
                        st.session_state.pop("uploaded_image_bytes", None)
                        st.session_state.pop("uploaded_image_name", None)

                    st.session_state["recipe"] = recipe
                    st.session_state["source_url"] = instagram_url.strip()
                    st.success("✅ 解析完了！下のプレビューを確認してください。")
                except json.JSONDecodeError:
                    st.error("JSONの解析に失敗しました。もう一度試してください。")
                except Exception as e:
                    st.error(f"解析エラー: {e}")


# ══════════════════════════════════════════════
# 共通：プレビュー & Notion保存
# ══════════════════════════════════════════════
if "recipe" in st.session_state:
    recipe = st.session_state["recipe"]
    source_url = st.session_state.get("source_url", "")

    st.divider()
    st.subheader("📋 プレビュー（編集してから保存できます）")

    col_l, col_r = st.columns([3, 2])
    with col_l:
        recipe["title"] = st.text_input("レシピ名", value=recipe.get("title", ""), key="preview_title")

        if recipe.get("description"):
            st.caption(recipe["description"][:200])

        m1, m2, m3 = st.columns(3)
        if recipe.get("servings"):   m1.metric("人数", recipe["servings"])
        if recipe.get("total_time"): m2.metric("調理時間", recipe["total_time"])
        if recipe.get("calories"):   m3.metric("カロリー", recipe["calories"])

        st.markdown("##### 🥕 材料")
        if recipe.get("ingredients"):
            for ing in recipe["ingredients"]:
                st.markdown(f"- {ing}")
        else:
            st.caption("材料を抽出できませんでした")

        st.markdown("##### 👨‍🍳 作り方")
        if recipe.get("instructions"):
            for i, step in enumerate(recipe["instructions"], 1):
                st.markdown(f"**{i}.** {step}")
        else:
            st.caption("作り方を抽出できませんでした")

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
                    # アップロード画像がある場合は Telegraph に送って URL を取得
                    img_bytes = st.session_state.get("uploaded_image_bytes")
                    if img_bytes:
                        with st.spinner("画像をアップロード中..."):
                            try:
                                img_name = st.session_state.get("uploaded_image_name", "image.jpg")
                                recipe["image_url"] = upload_image_telegraph(img_bytes, img_name)
                            except Exception as e:
                                st.warning(f"画像アップロード失敗（スキップして続行）: {e}")

                    page_url = save_to_notion(recipe, source_url)
                    st.success("✅ Notionに保存しました！")
                    st.markdown(f"[📖 Notionで開く]({page_url})")
                    st.balloons()
                    # 保存後は画像バイトを解放
                    st.session_state.pop("uploaded_image_bytes", None)
                except Exception as e:
                    st.error(f"保存エラー: {e}")
    with col_clear:
        if st.button("🗑️ クリア", use_container_width=True):
            del st.session_state["recipe"]
            st.rerun()
