"""
葛飾区行政手続き案内エージェント — Azure Functions バックエンド
================================================================
エンドポイント: POST /api/chat

リクエスト JSON:
    {
        "message":              "住民票の移動はどこでできますか？",
        "conversation_history": [{"role": "user"|"assistant", "content": "..."}],
        "detected_language":    "ja"   # Azure AI Speech が検出した言語コード
    }

レスポンス JSON:
    {
        "reply":             "転入届は区役所の窓口で...",
        "detected_language": "ja"
    }
"""

import json
import logging
import os

import azure.functions as func
import requests
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from openai import AzureOpenAI

# ---------------------------------------------------------------
# Azure クライアント初期化
# ---------------------------------------------------------------
openai_client = AzureOpenAI(
    azure_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key        = os.environ["AZURE_OPENAI_KEY"],
    api_version    = os.environ["AZURE_OPENAI_API_VERSION"],
)

search_client = SearchClient(
    endpoint   = os.environ["AZURE_SEARCH_ENDPOINT"],
    index_name = os.environ["AZURE_SEARCH_INDEX_NAME"],
    credential = AzureKeyCredential(os.environ["AZURE_SEARCH_KEY"]),
)

TRANSLATOR_ENDPOINT  = os.environ["AZURE_TRANSLATOR_ENDPOINT"]
TRANSLATOR_KEY       = os.environ["AZURE_TRANSLATOR_KEY"]
TRANSLATOR_REGION    = os.environ["AZURE_TRANSLATOR_REGION"]

GPT4O_DEPLOYMENT     = os.environ["AZURE_OPENAI_DEPLOYMENT_GPT4O"]
EMBEDDING_DEPLOYMENT = os.environ["AZURE_OPENAI_DEPLOYMENT_EMBEDDING"]
SPEECH_KEY           = os.environ.get("AZURE_SPEECH_KEY", "")
SPEECH_REGION        = os.environ.get("AZURE_SPEECH_REGION", "japaneast")

# ---------------------------------------------------------------
# Azure Functions アプリ定義
# ---------------------------------------------------------------
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@app.route(route="speech-token", methods=["GET"])
def speech_token(req: func.HttpRequest) -> func.HttpResponse:
    """
    Azure AI Speech の短期トークン（10分間有効）を発行して返す。
    フロントエンドはキーを直接持たず、このエンドポイント経由でトークンを取得する。
    """
    if not SPEECH_KEY:
        return _error_response("AZURE_SPEECH_KEY is not configured", 500)

    token_url = f"https://{SPEECH_REGION}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    headers   = {"Ocp-Apim-Subscription-Key": SPEECH_KEY}

    try:
        resp = requests.post(token_url, headers=headers, timeout=5)
        resp.raise_for_status()
        return func.HttpResponse(
            json.dumps({"token": resp.text, "region": SPEECH_REGION}),
            mimetype    = "application/json",
            status_code = 200,
        )
    except Exception as e:
        logging.exception("Speech トークン取得失敗")
        return _error_response(f"Failed to issue speech token: {str(e)}", 500)


@app.route(route="chat", methods=["POST"])
def chat(req: func.HttpRequest) -> func.HttpResponse:
    """メインのチャットエンドポイント"""
    logging.info("POST /api/chat called")

    # --- リクエストのパース ---
    try:
        body = req.get_json()
    except ValueError:
        return _error_response("Invalid JSON body", 400)

    user_message         = body.get("message", "").strip()
    conversation_history = body.get("conversation_history", [])
    detected_language    = body.get("detected_language", "ja")

    if not user_message:
        return _error_response("'message' is required", 400)

    try:
        # ① 入力を日本語に翻訳（RAG検索用）
        message_ja = translate(user_message, to_lang="ja", from_lang=detected_language)
        logging.info(f"[翻訳] {detected_language} → ja: {message_ja[:80]}")

        # ② RAG検索
        rag_context = search_rag(message_ja)
        logging.info(f"[RAG] コンテキスト取得完了")

        # ③ 回答生成（日本語）
        reply_ja = generate_answer(message_ja, rag_context, conversation_history)
        logging.info(f"[回答生成] {reply_ja[:80]}")

        # ④ 回答をユーザーの言語に翻訳
        reply = translate(reply_ja, to_lang=detected_language, from_lang="ja")
        logging.info(f"[翻訳] ja → {detected_language}: {reply[:80]}")

        return func.HttpResponse(
            json.dumps({
                "reply":             reply,
                "detected_language": detected_language,
            }, ensure_ascii=False),
            mimetype    = "application/json",
            status_code = 200,
        )

    except Exception as e:
        logging.exception("チャット処理中にエラーが発生しました")
        return _error_response(f"Internal server error: {str(e)}", 500)


# ---------------------------------------------------------------
# RAG 検索
# ---------------------------------------------------------------
def search_rag(query_ja: str, top_k: int = 6) -> str:
    """
    Azure AI Search でハイブリッド検索（キーワード + ベクトル）を実行し、
    関連チャンクをテキストで返す。カテゴリフィルタは使用せず全カテゴリ対象。
    """
    # クエリをベクトル化
    embedding_response = openai_client.embeddings.create(
        input = query_ja,
        model = EMBEDDING_DEPLOYMENT,
    )
    query_vector = embedding_response.data[0].embedding

    # ハイブリッド検索（キーワード + ベクトル）
    vector_query = VectorizedQuery(
        vector              = query_vector,
        k_nearest_neighbors = top_k,
        fields              = "content_vector",
    )

    results = search_client.search(
        search_text    = query_ja,
        vector_queries = [vector_query],
        top            = top_k,
        select         = ["title", "content", "source_url", "category"],
    )

    chunks = []
    for r in results:
        chunks.append(
            f"【{r['title']}】\n{r['content']}\n出典: {r['source_url']}"
        )

    return "\n\n---\n\n".join(chunks) if chunks else "関連情報が見つかりませんでした。"


# ---------------------------------------------------------------
# 回答生成
# ---------------------------------------------------------------
def generate_answer(
    message_ja: str,
    rag_context: str,
    history: list,
) -> str:
    """RAGコンテキストをもとに、GPT-4oで日本語回答を生成する"""

    system_prompt = f"""あなたは葛飾区の行政手続きを案内するAIアシスタントです。
日本人・外国人を問わず、葛飾区に住むすべての方の行政手続きをサポートします。

【参考情報（葛飾区公式サイトより）】
{rag_context}

【回答のルール】
1. ユーザーの質問に対して、関連する手続きを丁寧に案内してください
2. 必要に応じて「期限・必要書類・窓口・受付時間」を含めてください
3. 手続きが複数ある場合は、優先度の高いものから順番に説明してください
4. 回答は必ず日本語で生成してください（後で自動翻訳されます）
5. 出典URLを末尾に「参考リンク」としてまとめて記載してください
6. 外国人の方に関係する手続きが含まれる場合は、在留資格に関連する情報も補足してください
7. 参考情報に記載のない事柄については、「詳細は葛飾区役所にお問い合わせください」と案内してください
8. 簡潔かつ実用的な内容にまとめ、長すぎず・短すぎない回答を心がけてください""".strip()

    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-8:]:   # 直近8ターンの会話履歴を含める
        messages.append(h)
    messages.append({"role": "user", "content": message_ja})

    response = openai_client.chat.completions.create(
        model       = GPT4O_DEPLOYMENT,
        messages    = messages,
        temperature = 0.2,
        max_tokens  = 1800,  # 旧: 1000 → 切り捨て防止のため増量
    )
    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------
# Azure AI Translator
# ---------------------------------------------------------------
def translate(text: str, to_lang: str, from_lang: str = None) -> str:
    """
    Azure AI Translator で翻訳する。
    同じ言語の場合はそのまま返す。

    注意: Globalリソース（location=global）の場合は
    Ocp-Apim-Subscription-Region ヘッダーを送ってはいけない。
    Regionalリソースの場合のみヘッダーが必要。
    """
    if from_lang and from_lang == to_lang:
        return text
    if not text.strip():
        return text

    url    = f"{TRANSLATOR_ENDPOINT}/translate"
    params = {"api-version": "3.0", "to": to_lang}
    if from_lang:
        params["from"] = from_lang

    headers = {
        "Ocp-Apim-Subscription-Key": TRANSLATOR_KEY,
        "Content-Type":              "application/json",
    }
    # GlobalリソースはRegionヘッダー不要。Regionalリソースのみ付与する
    if TRANSLATOR_REGION and TRANSLATOR_REGION.lower() != "global":
        headers["Ocp-Apim-Subscription-Region"] = TRANSLATOR_REGION

    body = [{"text": text}]

    response = requests.post(url, params=params, headers=headers, json=body, timeout=10)
    response.raise_for_status()

    result = response.json()
    return result[0]["translations"][0]["text"]


# ---------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------
def _error_response(message: str, status: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"error": message}),
        mimetype    = "application/json",
        status_code = status,
    )
