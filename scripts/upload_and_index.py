"""
葛飾区データ アップロード & AI Search インデックス作成スクリプト
================================================================
setup_azure.sh でリソース作成後に実行してください。

実行方法:
    pip install openai azure-search-documents azure-storage-blob python-dotenv --break-system-packages
    python upload_and_index.py

処理の流れ:
    1. output/documents.json を Azure Blob Storage にアップロード
    2. Azure AI Search にインデックス（スキーマ）を作成
    3. 各ドキュメントのテキストを Embedding 化（text-embedding-3-small）
    4. ベクトルとメタデータを AI Search にアップロード
"""

import base64
import json
import os
import time
from pathlib import Path

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    SearchableField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()

# ---------------------------------------------------------------
# 設定
# ---------------------------------------------------------------
DOCUMENTS_PATH   = Path("./output/documents.json")
INDEX_NAME       = os.getenv("AZURE_SEARCH_INDEX_NAME", "katsushika-procedures")
EMBEDDING_DIM    = 1536   # text-embedding-3-small の次元数
BATCH_SIZE       = 10     # Embedding バッチサイズ（レート制限対策）

# Azure クライアント初期化
openai_client = AzureOpenAI(
    azure_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key        = os.environ["AZURE_OPENAI_KEY"],
    api_version    = os.environ["AZURE_OPENAI_API_VERSION"],
)

search_index_client = SearchIndexClient(
    endpoint   = os.environ["AZURE_SEARCH_ENDPOINT"],
    credential = AzureKeyCredential(os.environ["AZURE_SEARCH_KEY"]),
)

search_client = SearchClient(
    endpoint      = os.environ["AZURE_SEARCH_ENDPOINT"],
    index_name    = INDEX_NAME,
    credential    = AzureKeyCredential(os.environ["AZURE_SEARCH_KEY"]),
)

blob_service = BlobServiceClient.from_connection_string(
    os.environ["AZURE_STORAGE_CONNECTION_STRING"]
)
container_name = os.environ["AZURE_STORAGE_CONTAINER"]


# ---------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------
def safe_key(key: str) -> str:
    """
    Azure AI Search のドキュメントキーに使えない文字（日本語など）を
    URL安全な Base64 でエンコードする。
    キーに使えるのは英数字・アンダースコア・ハイフン・イコールのみ。
    """
    return base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------
# STEP 1: Blob Storage にアップロード
# ---------------------------------------------------------------
def upload_to_blob():
    print("\n[STEP 1] Blob Storage にドキュメントをアップロード中...")

    container_client = blob_service.get_container_client(container_name)

    with open(DOCUMENTS_PATH, "rb") as f:
        container_client.upload_blob(
            name      = "documents.json",
            data      = f,
            overwrite = True,
        )

    print(f"  ✅ アップロード完了: {DOCUMENTS_PATH} → {container_name}/documents.json")


# ---------------------------------------------------------------
# STEP 2: AI Search インデックス作成
# ---------------------------------------------------------------
def create_search_index():
    print(f"\n[STEP 2] AI Search インデックス '{INDEX_NAME}' を作成中...")

    fields = [
        # キーフィールド
        SimpleField(
            name       = "id",
            type       = SearchFieldDataType.String,
            key        = True,
            filterable = True,
        ),
        # 検索対象フィールド
        SearchableField(
            name       = "title",
            type       = SearchFieldDataType.String,
            analyzer_name = "ja.microsoft",   # 日本語形態素解析
        ),
        SearchableField(
            name       = "content",
            type       = SearchFieldDataType.String,
            analyzer_name = "ja.microsoft",
        ),
        # フィルタ・ファセット用フィールド
        SimpleField(
            name       = "category",
            type       = SearchFieldDataType.String,
            filterable = True,
            facetable  = True,
        ),
        SimpleField(
            name       = "priority",
            type       = SearchFieldDataType.String,
            filterable = True,
        ),
        SimpleField(
            name       = "source_url",
            type       = SearchFieldDataType.String,
            filterable = True,
        ),
        SimpleField(
            name       = "label",
            type       = SearchFieldDataType.String,
        ),
        SimpleField(
            name       = "chunk_index",
            type       = SearchFieldDataType.Int32,
        ),
        SimpleField(
            name       = "scraped_at",
            type       = SearchFieldDataType.String,
        ),
        # ベクトルフィールド（ハイブリッド検索用）
        SearchField(
            name                  = "content_vector",
            type                  = SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable            = True,
            vector_search_dimensions = EMBEDDING_DIM,
            vector_search_profile_name = "hnsw-profile",
        ),
    ]

    vector_search = VectorSearch(
        algorithms = [HnswAlgorithmConfiguration(name="hnsw-config")],
        profiles   = [VectorSearchProfile(
            name                    = "hnsw-profile",
            algorithm_configuration_name = "hnsw-config",
        )],
    )

    semantic_search = SemanticSearch(
        configurations = [SemanticConfiguration(
            name = "semantic-config",
            prioritized_fields = SemanticPrioritizedFields(
                title_field          = SemanticField(field_name="title"),
                content_fields       = [SemanticField(field_name="content")],
                keywords_fields      = [SemanticField(field_name="category")],
            ),
        )]
    )

    index = SearchIndex(
        name          = INDEX_NAME,
        fields        = fields,
        vector_search = vector_search,
        semantic_search = semantic_search,
    )

    search_index_client.create_or_update_index(index)
    print(f"  ✅ インデックス作成完了: {INDEX_NAME}")
    print(f"     フィールド数: {len(fields)}（ベクトル次元: {EMBEDDING_DIM}）")


# ---------------------------------------------------------------
# STEP 3: Embedding 生成
# ---------------------------------------------------------------
def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """テキストリストを Embedding ベクトルに変換する（バッチ処理）"""
    all_vectors = []
    deployment  = os.environ["AZURE_OPENAI_DEPLOYMENT_EMBEDDING"]

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        print(f"    Embedding生成中: {i+1}〜{min(i+len(batch), len(texts))} / {len(texts)} チャンク")

        response = openai_client.embeddings.create(
            input = batch,
            model = deployment,
        )
        vectors = [item.embedding for item in response.data]
        all_vectors.extend(vectors)
        time.sleep(0.5)  # レート制限対策

    return all_vectors


# ---------------------------------------------------------------
# STEP 4: ドキュメントを AI Search にアップロード
# ---------------------------------------------------------------
def upload_to_search():
    print("\n[STEP 3 & 4] Embedding 生成 + AI Search へアップロード中...")

    with open(DOCUMENTS_PATH, encoding="utf-8") as f:
        documents = json.load(f)

    print(f"  ドキュメント数: {len(documents)}")

    # Embedding 生成
    texts   = [doc["content"] for doc in documents]
    vectors = generate_embeddings(texts)

    # ドキュメントにベクトルを付与 + IDをBase64エンコード
    for doc, vec in zip(documents, vectors):
        doc["content_vector"] = vec
        # Azure AI Search はキーに日本語不可 → URL安全なBase64に変換
        doc["id"] = safe_key(doc["id"])
        # total_chunks は AI Search スキーマにないので除外
        doc.pop("total_chunks", None)

    # バッチアップロード（AI Search は1リクエスト最大1000件）
    print(f"\n  AI Search にアップロード中...")
    results = search_client.upload_documents(documents=documents)

    succeeded = sum(1 for r in results if r.succeeded)
    failed    = sum(1 for r in results if not r.succeeded)
    print(f"  ✅ アップロード完了: 成功 {succeeded} 件 / 失敗 {failed} 件")

    if failed > 0:
        for r in results:
            if not r.succeeded:
                print(f"  ❌ 失敗: id={r.key}, error={r.error_message}")


# ---------------------------------------------------------------
# STEP 5: 動作確認クエリ
# ---------------------------------------------------------------
def verify_index():
    print("\n[STEP 5] インデックスの動作確認...")

    # テストクエリ（日本語）
    results = search_client.search(
        search_text  = "転入届 外国人 必要書類",
        top          = 3,
        select       = ["id", "title", "category", "source_url"],
    )

    print("  テストクエリ「転入届 外国人 必要書類」の結果:")
    for r in results:
        print(f"    - [{r['category']}] {r['title']}")
        print(f"      {r['source_url']}")

    print("\n  ✅ インデックス動作確認完了")


# ---------------------------------------------------------------
# メイン
# ---------------------------------------------------------------
def main():
    print("=" * 60)
    print("  葛飾区データ アップロード & インデックス作成")
    print("=" * 60)

    upload_to_blob()
    create_search_index()
    upload_to_search()
    verify_index()

    print("\n" + "=" * 60)
    print("  ✅ 全工程完了！")
    print("=" * 60)
    print("\n  次のステップ: Azure Functions の API 実装へ進んでください。")


if __name__ == "__main__":
    main()
