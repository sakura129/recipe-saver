"""
🍳 レシピ帳 — URLまたはテキストからNotionに自動保存
対応: クックパッド / Kurashiru / デリッシュキッチン / Allrecipes / YouTube / Instagram（テキスト貼り付け）他
"""

import streamlit as st
import requests
from bs4 import BeautifulSoup
import json
import re
import base64
from urllib.parse import urlparse

# ─────────────────────────────────────────────
# 設定読み込み（Streamlit Cloud の Secrets から）
# ─────────────────────────────────────────────
NOTION_TOKEN   = st.secrets.get("NOTION_TOKEN", "")
NOTION_DB_ID   = st.secrets.get("NOTION_DB_ID", "4d144a7a9484495abb8938cf193d7f5e")
ANTHROPIC_KEY  = st.secrets.get("ANTHROPIC_API_KEY", "")
NOTION_VERSION = "2026-03-11"

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
# Claude Vision API で画像（写真・スクショ）からレシピを抽出
# ─────────────────────────────────────────────
def _media_type(filename: str) -> str:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "jpg").lower()
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")


def extract_from_images_with_claude(images: list[tuple[bytes, str]]) -> dict:
    """
    Instagramのスクリーンショットや、料理写真・レシピカードの撮影画像から
    Claude Vision APIで直接レシピ情報を抽出する（コピペ不要）。
    images: [(画像バイト列, ファイル名), ...]　最大5枚まで使用
    """
    import anthropic  # type: ignore

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    content: list[dict] = []
    for img_bytes, filename in images[:5]:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": _media_type(filename),
                "data": base64.b64encode(img_bytes).decode("utf-8"),
            },
        })

    prompt = """この画像は料理レシピのスクリーンショットまたは写真です
（Instagramの投稿、レシピサイトの画面、レシピカード、雑誌のページなど）。
画像内の文字や写真の内容から、以下の情報をJSON形式で抽出してください。

抽出項目:
- title: レシピ名（写真の料理名から推測してもよい。なければ「レシピ」）
- servings: 何人分か（例: "2人分"。記載なければ空文字）
- total_time: 調理時間（例: "20分"。記載なければ空文字）
- calories: カロリー（例: "300kcal"。記載なければ空文字）
- ingredients: 材料のリスト（各要素は「食材名 量」の文字列。最大30個）
- instructions: 作り方のステップリスト（番号を除いた手順文。最大20ステップ）

ルール:
- 必ずJSONのみを返す（説明文は不要）
- ingredientsとinstructionsは文字列の配列で返す
- 複数枚の画像がある場合はすべてを統合して1つのレシピとしてまとめる
- 情報がない項目は空文字または空配列にする
"""
    content.append({"type": "text", "text": prompt})

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": content}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    parsed = json.loads(raw)

    return {
        "title":        parsed.get("title", "レシピ") or "レシピ",
        "description":  "",
        "servings":     parsed.get("servings", ""),
        "total_time":   parsed.get("total_time", ""),
        "calories":     parsed.get("calories", ""),
        "image_url":    "",
        "ingredients":  parsed.get("ingredients", []),
        "instructions": parsed.get("instructions", []),
        "source_tag":   "Instagram",
    }


# ─────────────────────────────────────────────
# 画像をNotion公式 File Upload APIでアップロード
# ─────────────────────────────────────────────
def upload_image_to_notion(image_bytes: bytes, filename: str = "image.jpg") -> str | None:
    """
    Notion公式の File Upload API を使って画像をアップロードし、
    file_upload オブジェクトの ID を返す。
    （外部の画像ホスティングサービスを使わないため、Notion側での
      画像表示エラーが発生しない）
    失敗した場合は None を返す。
    """
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "jpg").lower()
    ct = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
          "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
    }

    try:
        # 1. アップロード用オブジェクトを作成（filename と content_type を必ず含める）
        create_resp = requests.post(
            "https://api.notion.com/v1/file_uploads",
            headers={**headers, "Content-Type": "application/json"},
            json={"filename": filename, "content_type": ct},
            timeout=15,
        )
        create_resp.raise_for_status()
        upload_info = create_resp.json()
        upload_id   = upload_info["id"]
        upload_url  = upload_info["upload_url"]

        # 2. ファイル本体を multipart/form-data で送信
        #    ※ requests が files= を渡すと Content-Type ヘッダーを自動設定するため
        #      Authorization と Notion-Version だけを明示する
        send_resp = requests.post(
            upload_url,
            headers={
                "Authorization": f"Bearer {NOTION_TOKEN}",
                "Notion-Version": NOTION_VERSION,
            },
            files={"file": (filename, image_bytes, ct)},
            timeout=30,
        )
        send_resp.raise_for_status()

        return upload_id
    except Exception as e:
        st.warning(f"画像のNotionアップロードに失敗しました: {e}")
        return None


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

    # Notionにアップロードした画像を本文の先頭に埋め込む
    if recipe.get("image_file_upload_id"):
        children.append({
            "object": "block", "type": "image",
            "image": {"type": "file_upload", "file_upload": {"id": recipe["image_file_upload_id"]}},
        })

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

    # カバー画像（Notionアップロード画像があれば優先、なければ外部URL）
    cover = None
    if recipe.get("image_file_upload_id"):
        cover = {"type": "file_upload", "file_upload": {"id": recipe["image_file_upload_id"]}}
    elif recipe.get("image_url"):
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
st.caption("URLを貼る／写真をアップロード／テキストを貼るだけでNotionに自動保存")

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
tab_url, tab_photo, tab_text = st.tabs([
    "🔗 URLから保存（レシピサイト・YouTube）",
    "📷 写真・スクショから保存（いちばん簡単）",
    "📝 テキストから保存（手動貼り付け）",
])


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
            st.warning("Instagramはログインが必要なため、URLからの自動取得には対応していません。「📷 写真・スクショから保存」タブをご利用ください 👉")
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
# TAB 2 : 写真・スクリーンショットモード（コピペ不要・最速）
# ══════════════════════════════════════════════
with tab_photo:
    st.markdown("""
    **使い方（コピペ一切不要）**
    1. Instagramの投稿画面をそのまま**スクリーンショット**（写真とキャプションが収まるように。長い場合は2〜3枚に分けてOK）
       ※ 料理写真やレシピカード・雑誌のページを**撮影**してもOK
    2. 下にアップロードして「✨ 解析してNotionに保存」を1回タップ

    画像に写っている文字をAIがそのまま読み取るので、コピーや貼り付けは不要です。
    """)

    photo_files = st.file_uploader(
        "🖼️ 画像をアップロード（複数選択可）",
        type=["jpg", "jpeg", "png", "webp"],
        key="photo_uploads",
        accept_multiple_files=True,
        help="スマホのカメラロール・スクリーンショットから選べます。",
    )
    if photo_files:
        cols = st.columns(min(len(photo_files), 4))
        for i, f in enumerate(photo_files):
            with cols[i % len(cols)]:
                st.image(f, use_container_width=True)

    with st.expander("🔗 元のURLを記録する（任意）"):
        photo_source_url = st.text_input(
            "Instagramなどの投稿URL",
            key="photo_source_url",
            placeholder="https://www.instagram.com/p/xxxxx/",
        )

    photo_btn = st.button("✨ 解析してNotionに保存", type="primary", key="btn_photo", use_container_width=True)

    if photo_btn:
        if not photo_files:
            st.warning("画像をアップロードしてください。")
        elif not ANTHROPIC_KEY:
            st.error(
                "⚠️ ANTHROPIC_API_KEY が設定されていません。\n\n"
                "Streamlit Cloud の Secrets に `ANTHROPIC_API_KEY = \"sk-ant-xxxx\"` を追加してください。"
            )
        else:
            try:
                images = [(f.getvalue(), f.name) for f in photo_files]

                with st.spinner("① Claude AIで画像を解析中..."):
                    recipe = extract_from_images_with_claude(images)

                src_url = st.session_state.get("photo_source_url", "").strip()
                if src_url:
                    recipe["source_tag"] = detect_source_tag(src_url)

                with st.spinner("② 画像をNotionにアップロード中..."):
                    upload_id = upload_image_to_notion(images[0][0], images[0][1])
                    if upload_id:
                        recipe["image_file_upload_id"] = upload_id

                with st.spinner("③ Notionに保存中..."):
                    page_url = save_to_notion(recipe, src_url)

                st.success("✅ Notionに保存しました！")
                st.markdown(f"[📖 Notionで開く]({page_url})")
                st.balloons()

                with st.expander("📋 抽出された内容を確認（違っていたらNotion側で編集してください）"):
                    st.markdown(f"**{recipe.get('title', '')}**")
                    m1, m2, m3 = st.columns(3)
                    if recipe.get("servings"):   m1.metric("人数", recipe["servings"])
                    if recipe.get("total_time"): m2.metric("調理時間", recipe["total_time"])
                    if recipe.get("calories"):   m3.metric("カロリー", recipe["calories"])
                    st.markdown("##### 🥕 材料")
                    for ing in recipe.get("ingredients", []):
                        st.markdown(f"- {ing}")
                    st.markdown("##### 👨‍🍳 作り方")
                    for i, step in enumerate(recipe.get("instructions", []), 1):
                        st.markdown(f"**{i}.** {step}")

            except json.JSONDecodeError:
                st.error("AIによる解析に失敗しました（JSON形式エラー）。もう一度試してください。")
            except Exception as e:
                st.error(f"エラー: {e}")


# ══════════════════════════════════════════════
# TAB 3 : テキスト貼り付けモード（手動）
# ══════════════════════════════════════════════
with tab_text:
    st.markdown("""
    **テキストを貼り付けて保存（写真がない場合・手動入力向け）**
    1. キャプションや材料・作り方のテキストを下のボックスにまとめて貼り付け（InstagramのURLも一緒に貼ってOK）
    2. 画像を選んで「✨ 解析してNotionに保存」を1回タップ

    💡 Instagramのスクショがある場合は「📷 写真・スクショから保存」タブの方が簡単です。
    """)

    col_img, col_paste = st.columns([1, 2])

    with col_img:
        uploaded_file = st.file_uploader(
            "🖼️ 画像（任意）",
            type=["jpg", "jpeg", "png", "webp"],
            key="uploaded_image",
            help="スマホのカメラロールから選べます。Notionのカバー画像＆本文に追加されます。",
        )
        if uploaded_file:
            st.image(uploaded_file, use_container_width=True)

    with col_paste:
        pasted_text = st.text_area(
            "📋 InstagramのURL＋キャプションをまとめて貼り付け",
            height=220,
            placeholder="https://www.instagram.com/p/xxxxx/\n\n材料（2人分）\n鶏もも肉 300g\n醤油 大さじ2\n...\n\n作り方\n①鶏肉を一口大に切る\n②フライパンで焼く...",
            key="pasted_text",
        )

    one_tap_btn = st.button("✨ 解析してNotionに保存", type="primary", key="btn_text", use_container_width=True)

    if one_tap_btn:
        if not pasted_text.strip():
            st.warning("テキストを貼り付けてください。")
        elif not ANTHROPIC_KEY:
            st.error(
                "⚠️ ANTHROPIC_API_KEY が設定されていません。\n\n"
                "Streamlit Cloud の Secrets に `ANTHROPIC_API_KEY = \"sk-ant-xxxx\"` を追加してください。"
            )
        else:
            # 貼り付けテキストからInstagramのURLを自動検出し、本文から取り除く
            url_match = re.search(r"https?://(?:www\.)?instagram\.com/\S+", pasted_text)
            instagram_url = ""
            text_for_ai = pasted_text
            if url_match:
                instagram_url = url_match.group(0).rstrip(".,、。　 ")
                text_for_ai = pasted_text.replace(url_match.group(0), "").strip()

            try:
                with st.spinner("① Claude AIでレシピを解析中..."):
                    recipe = extract_from_text_with_claude(text_for_ai)
                    recipe["source_tag"] = "Instagram"

                if uploaded_file:
                    with st.spinner("② 画像をNotionにアップロード中..."):
                        upload_id = upload_image_to_notion(uploaded_file.getvalue(), uploaded_file.name)
                        if upload_id:
                            recipe["image_file_upload_id"] = upload_id

                with st.spinner("③ Notionに保存中..."):
                    page_url = save_to_notion(recipe, instagram_url)

                st.success("✅ Notionに保存しました！")
                st.markdown(f"[📖 Notionで開く]({page_url})")
                st.balloons()

                with st.expander("📋 抽出された内容を確認（違っていたらNotion側で編集してください）"):
                    st.markdown(f"**{recipe.get('title', '')}**")
                    m1, m2, m3 = st.columns(3)
                    if recipe.get("servings"):   m1.metric("人数", recipe["servings"])
                    if recipe.get("total_time"): m2.metric("調理時間", recipe["total_time"])
                    if recipe.get("calories"):   m3.metric("カロリー", recipe["calories"])
                    st.markdown("##### 🥕 材料")
                    for ing in recipe.get("ingredients", []):
                        st.markdown(f"- {ing}")
                    st.markdown("##### 👨‍🍳 作り方")
                    for i, step in enumerate(recipe.get("instructions", []), 1):
                        st.markdown(f"**{i}.** {step}")

            except json.JSONDecodeError:
                st.error("AIによる解析に失敗しました（JSON形式エラー）。もう一度試してください。")
            except Exception as e:
                st.error(f"エラー: {e}")


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
