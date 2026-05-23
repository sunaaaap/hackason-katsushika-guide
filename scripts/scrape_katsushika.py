"""
葛飾区サイト データ収集スクリプト
===================================
収集したデータを Azure AI Search 用の JSON 形式で保存します。

実行方法:
    pip install requests beautifulsoup4
    python scrape_katsushika.py

出力:
    ./output/documents.json        （Azure AI Search インデックス用）
    ./output/documents_preview.md  （人間が確認用のプレビュー）
"""

import json
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------
# 設定
# ---------------------------------------------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ResearchBot/1.0; +personal-hackathon)"
}

OUTPUT_DIR = Path("./output")

# 収集対象ページ（ラベル・URL・カテゴリ・優先度）
# スコープ拡張: 日本人・外国人を問わず葛飾区の行政手続き全般をカバー
PAGES = [
    # label, url, category, priority

    # --- 住民票・転入転出 ---
    ("住民異動届（転入・転出・転居）", "https://www.city.katsushika.lg.jp/kurashi/1000046/1001401/1001438.html",           "residence",        "high"),
    ("転入届（外国人）",              "https://www.city.katsushika.lg.jp/kurashi/1000046/1001404/1001451.html",            "residence",        "high"),
    ("外国人住所変更FAQ",             "https://www.city.katsushika.lg.jp/faq/1030270/1007654/1007709/1007934.html",        "residence",        "high"),
    ("外国人の手続き（多言語）",      "https://www.city.katsushika.lg.jp/information/1000087/1038058/1038062.html",        "residence",        "high"),

    # --- 戸籍 ---
    ("戸籍謄本・抄本の取得",         "https://www.city.katsushika.lg.jp/kurashi/1000046/1001404/1001432.html",            "family-registry",  "high"),
    ("出生届",                        "https://www.city.katsushika.lg.jp/kurashi/1000046/1001404/1001409.html",            "family-registry",  "high"),
    ("婚姻届",                        "https://www.city.katsushika.lg.jp/kurashi/1000046/1001404/1001412.html",            "family-registry",  "high"),
    ("死亡届",                        "https://www.city.katsushika.lg.jp/kurashi/1000046/1001404/1001418.html",            "family-registry",  "high"),

    # --- 印鑑登録・各種証明書 ---
    ("印鑑登録",                      "https://www.city.katsushika.lg.jp/kurashi/1000046/1001401/1001443.html",            "certificate",      "high"),
    ("各種証明書の発行",              "https://www.city.katsushika.lg.jp/kurashi/1000046/1001401/1001446.html",            "certificate",      "high"),

    # --- マイナンバー ---
    ("マイナンバーカードの申請",      "https://www.city.katsushika.lg.jp/kurashi/1000046/1001401/1001460.html",            "my-number",        "high"),
    ("届出とマイナンバー",            "https://www.city.katsushika.lg.jp/kurashi/1000049/1001690/1010236.html",            "my-number",        "high"),

    # --- 健康保険・年金 ---
    ("国民健康保険の届出",            "https://www.city.katsushika.lg.jp/kurashi/1000049/1001690/1001699.html",            "health-insurance", "high"),
    ("国民年金加入手続き",            "https://www.city.katsushika.lg.jp/kurashi/1000049/1001692/1001772.html",            "pension",          "high"),

    # --- 子育て ---
    ("児童手当",                      "https://www.city.katsushika.lg.jp/kurashi/1000050/1001726/1001731.html",            "child",            "high"),
    ("保育施設の入所申請",            "https://www.city.katsushika.lg.jp/kurashi/1000050/1001726/1001737.html",            "child",            "high"),

    # --- 税金 ---
    ("住民税（特別区民税・都民税）",  "https://www.city.katsushika.lg.jp/kurashi/1000048/1001676/1001677.html",            "tax",              "medium"),

    # --- 生活・その他 ---
    ("ごみ・リサイクル",              "https://www.city.katsushika.lg.jp/kurashi/1000057/index.html",                      "daily-life",       "medium"),
    ("外国人向け生活ガイド",          "https://www.city.katsushika.lg.jp/information/1000087/1022737/1006626/1028286/index.html", "daily-life", "medium"),
    ("外国人生活相談",                "https://www.city.katsushika.lg.jp/kurashi/1000061/1003798/1003866.html",            "consultation",     "medium"),
    ("区役所・窓口案内",              "https://www.city.katsushika.lg.jp/kurashi/1000046/1001401/index.html",              "office-info",      "medium"),
]


# ---------------------------------------------------------------
# テキスト整形ユーティリティ
# ---------------------------------------------------------------
def normalize_text(text: str) -> str:
    """全角スペース・改行の正規化"""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\n{3,}", "\n\n", text)       # 3行以上の空行を2行に
    text = re.sub(r"[ \t]+", " ", text)           # 連続スペースを1つに
    text = re.sub(r" \n", "\n", text)             # 行末スペース除去
    return text.strip()


def extract_main_text(soup: BeautifulSoup) -> str:
    """ナビ・フッターなどのノイズを除いた本文テキストを取得"""
    # ノイズタグを除去
    for tag in soup(["nav", "header", "footer", "script", "style",
                     "noscript", "aside", "form"]):
        tag.decompose()

    # クラス名でノイズっぽい要素を除去（サイドバー・パンくず等）
    noise_patterns = ["breadcrumb", "sidebar", "sns", "share",
                      "related", "recommend", "banner", "ad-"]
    for pattern in noise_patterns:
        for tag in soup.find_all(class_=re.compile(pattern, re.I)):
            tag.decompose()

    # 本文候補を優先度順に探す
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(id=re.compile(r"main|content|body", re.I))
        or soup.find(class_=re.compile(r"main|content|body", re.I))
        or soup.body
    )

    return main.get_text(separator="\n", strip=True) if main else ""


def extract_title(soup: BeautifulSoup, fallback: str) -> str:
    """ページタイトルを取得"""
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    title_tag = soup.find("title")
    if title_tag:
        # 「〇〇 | 葛飾区公式サイト」から前半だけ抽出
        return title_tag.get_text(strip=True).split("|")[0].strip()
    return fallback


def chunk_text(text: str, max_chars: int = 1000, overlap: int = 100) -> list[str]:
    """
    長文を Azure AI Search に適したサイズに分割する。
    段落（空行）を優先的に区切りとして使用し、
    それでも長い場合は max_chars で強制分割。
    overlap で前後の文脈を少し持たせる。
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            # 段落自体が max_chars を超える場合は強制分割
            if len(para) > max_chars:
                for i in range(0, len(para), max_chars - overlap):
                    chunks.append(para[i:i + max_chars])
            else:
                current = para

    if current:
        chunks.append(current)

    return chunks


# ---------------------------------------------------------------
# メインの収集処理
# ---------------------------------------------------------------
def scrape_page(label: str, url: str, category: str, priority: str) -> list[dict]:
    """1ページを収集し、Azure AI Search 用ドキュメントのリストを返す"""
    print(f"  取得中: {label} ...")
    try:
        res = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, "html.parser")

        title = extract_title(soup, label)
        raw_text = extract_main_text(soup)
        clean_text = normalize_text(raw_text)
        chunks = chunk_text(clean_text)

        documents = []
        for i, chunk in enumerate(chunks):
            doc = {
                # Azure AI Search の必須フィールド
                "id": f"{category}_{label}_{i}".replace(" ", "_").replace("（", "").replace("）", ""),
                # 検索・回答生成に使うフィールド
                "title": title,
                "content": chunk,
                "source_url": url,
                "category": category,        # move-in / health-insurance / pension / etc.
                "priority": priority,        # high / medium
                "label": label,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "scraped_at": datetime.now().isoformat(),
            }
            documents.append(doc)

        print(f"    ✅ {len(chunks)} チャンクに分割 ({len(clean_text):,} 文字)")
        return documents

    except Exception as e:
        print(f"    ❌ 失敗: {e}")
        return []


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_documents = []

    print("=" * 60)
    print("  葛飾区サイト データ収集")
    print("=" * 60)

    for label, url, category, priority in PAGES:
        docs = scrape_page(label, url, category, priority)
        all_documents.extend(docs)
        time.sleep(1.5)  # サーバー負荷軽減

    # -------------------------------------------------------
    # ① Azure AI Search 用 JSON 出力
    # -------------------------------------------------------
    json_path = OUTPUT_DIR / "documents.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_documents, f, ensure_ascii=False, indent=2)

    print(f"\n✅ JSON 出力: {json_path} （{len(all_documents)} ドキュメント）")

    # -------------------------------------------------------
    # ② 人間確認用 Markdown プレビュー出力
    # -------------------------------------------------------
    md_path = OUTPUT_DIR / "documents_preview.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 葛飾区 収集データ プレビュー\n\n")
        f.write(f"収集日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"総ドキュメント数: {len(all_documents)}\n\n---\n\n")

        for doc in all_documents:
            f.write(f"## {doc['title']}（チャンク {doc['chunk_index'] + 1}/{doc['total_chunks']}）\n\n")
            f.write(f"- **カテゴリ**: {doc['category']}\n")
            f.write(f"- **URL**: {doc['source_url']}\n\n")
            f.write(f"```\n{doc['content'][:500]}{'...' if len(doc['content']) > 500 else ''}\n```\n\n---\n\n")

    print(f"✅ プレビュー出力: {md_path}")

    # -------------------------------------------------------
    # ③ カテゴリ別サマリー
    # -------------------------------------------------------
    print("\n【カテゴリ別チャンク数】")
    from collections import Counter
    counts = Counter(d["category"] for d in all_documents)
    for cat, count in sorted(counts.items()):
        print(f"  {cat:<25} : {count} チャンク")

    print(f"\n合計: {len(all_documents)} チャンク → Azure Blob Storage にアップロード後、AI Search でインデックス化")


if __name__ == "__main__":
    main()
