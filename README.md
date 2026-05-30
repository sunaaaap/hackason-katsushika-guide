# 葛飾区 行政手続き案内エージェント

葛飾区に住むすべての方（日本人・外国人問わず）が、行政手続きに関する質問をテキストまたは音声で問い合わせると、葛飾区公式サイトの情報をもとに AI が回答する チャットボットです。

> **Microsoft Agent Hackathon 2026** 個人参加作品

[!WARNING]
本プロジェクトは葛飾区が公認・提供するサービスではありません。個人が学習・研究目的で開発したものであり、葛飾区および葛飾区役所とは一切関係ありません。

---

## デモ動画

https://youtu.be/N6VC5mKmXao

---

## 機能

- **キーボード入力・音声入力** によるチャット
- **RAG（Retrieval-Augmented Generation）** による公式情報ベースの回答
- **多言語対応**：日本語以外の言語で入力しても日本語インデックスを検索し、入力言語で回答
- **参考リンク表示**：回答末尾に葛飾区公式サイトの出典 URL を明示
- **アクセス制御**：Microsoft（Entra ID）認証 + カスタムロールによる招待制

---

## アーキテクチャ

```
[ユーザー（ブラウザ）]
    キーボード入力 / 音声入力（Azure AI Speech SDK）
         │ HTTPS /api/*
         ▼
[Azure Static Web Apps]
    ├── index.html（フロントエンド）
    └── 管理 Azure Functions（api/）
         ├── POST /api/chat
         │     ① 入力を日本語に翻訳（Azure AI Translator）
         │     ② RAG ハイブリッド検索（Azure AI Search）
         │     ③ 回答生成（Azure OpenAI GPT-4o）
         │     ④ 回答を入力言語に翻訳して返却
         └── GET /api/speech-token
               Speech 短期トークン発行（10分間有効）

[CI/CD]
    GitHub（main push）→ Azure Pipelines → Azure Static Web Apps デプロイ
```

---

## 技術スタック

| カテゴリ | 技術 |
|---------|------|
| フロントエンド | HTML / CSS / JavaScript（Vanilla JS）|
| バックエンド | Python 3.11（Azure Functions v2）|
| ホスティング | Azure Static Web Apps |
| 回答生成 | Azure OpenAI（GPT-4o）|
| ベクトル検索 | Azure AI Search（BM25 + HNSW ハイブリッド）|
| エンベディング | Azure OpenAI（text-embedding-3-small / 1,536次元）|
| 翻訳 | Azure AI Translator |
| 音声入力 | Azure AI Speech SDK（連続認識）|
| CI/CD | GitHub + Azure Pipelines |

---

## ディレクトリ構成

```
.
├── index.html                  # フロントエンド
├── staticwebapp.config.json    # SWA 設定（ルーティング・認証）
├── azure-pipelines.yml         # CI/CD パイプライン定義
├── scrape_katsushika.py        # データ収集スクリプト（事前処理）
├── .gitignore
└── api/
    ├── function_app.py         # Azure Functions エンドポイント
    ├── requirements.txt        # Python 依存パッケージ
    └── local.settings.json     # ローカル開発用環境変数（※ Git 管理対象外）
```

---

## ローカル開発環境のセットアップ

### 前提条件

- Python 3.11
- [Azure Functions Core Tools v4](https://learn.microsoft.com/ja-jp/azure/azure-functions/functions-run-local)
- Azure サブスクリプション（各サービスのリソース作成済み）

### 手順

**1. リポジトリをクローン**

```bash
git clone https://github.com/<your-repo>.git
cd <your-repo>
```

**2. Python 仮想環境を作成**

```bash
cd api
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**3. ローカル環境変数を設定**

`api/local.settings.json` を作成し、以下の内容を記載します（このファイルは `.gitignore` に含まれており Git 管理対象外です）。

```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "AZURE_OPENAI_ENDPOINT": "<Azure OpenAI エンドポイント>",
    "AZURE_OPENAI_KEY": "<Azure OpenAI APIキー>",
    "AZURE_OPENAI_API_VERSION": "<APIバージョン（例：2024-02-01）>",
    "AZURE_OPENAI_DEPLOYMENT_GPT4O": "<GPT-4o デプロイ名>",
    "AZURE_OPENAI_DEPLOYMENT_EMBEDDING": "<Embedding デプロイ名>",
    "AZURE_SEARCH_ENDPOINT": "<Azure AI Search エンドポイント>",
    "AZURE_SEARCH_KEY": "<Azure AI Search APIキー>",
    "AZURE_SEARCH_INDEX_NAME": "<インデックス名>",
    "AZURE_TRANSLATOR_ENDPOINT": "<Translator エンドポイント>",
    "AZURE_TRANSLATOR_KEY": "<Translator APIキー>",
    "AZURE_TRANSLATOR_REGION": "<リージョン（Globalリソースの場合は global）>",
    "AZURE_SPEECH_KEY": "<Azure AI Speech APIキー>",
    "AZURE_SPEECH_REGION": "<リージョン（例：japaneast）>"
  }
}
```

**4. Functions をローカル起動**

```bash
cd api
func start
```

**5. フロントエンドをブラウザで確認**

`index.html` をブラウザで直接開く（または Live Server 等を使用）。ローカル起動時は `http://localhost:7071/api` に接続します。

---

## デプロイ

### Azure Static Web Apps への自動デプロイ

`main` ブランチへの push をトリガーに Azure Pipelines が自動デプロイを実行します。

**Pipeline Variables に設定が必要なシークレット**

| 変数名 | 説明 |
|-------|------|
| `AZURE_STATIC_WEB_APPS_API_TOKEN` | SWA のデプロイトークン |

**SWA Application Settings に設定が必要な環境変数**

Azure Portal → Static Web App → 設定 → 環境変数 に、`local.settings.json` の `Values` 内の各キーと同じ名前で登録してください。

---

## データ収集（事前処理）

`scrape_katsushika.py` を実行すると、葛飾区公式サイトから行政手続きの情報を取得します。取得したテキストをチャンキング・エンベディングして Azure AI Search にインデックス登録することで、RAG 検索が機能します。

```bash
python scrape_katsushika.py
```

> **注意**: スクレイピングの実行前に葛飾区公式サイトの利用規約を確認してください。

---

## アクセス制御

`staticwebapp.config.json` でカスタムロール `reviewer` を定義しています。Azure Portal の SWA ロール管理画面から招待リンクを発行してロールを付与することで、特定ユーザーのみアクセスを許可できます。招待リンクの有効期限は最大 7 日間です。

---

## 参考リンク

- [Azure Static Web Apps ドキュメント](https://learn.microsoft.com/ja-jp/azure/static-web-apps/overview)
- [Azure OpenAI Service ドキュメント](https://learn.microsoft.com/ja-jp/azure/ai-services/openai/overview)
- [Azure AI Search ドキュメント](https://learn.microsoft.com/ja-jp/azure/search/search-what-is-azure-search)
- [Azure AI Translator ドキュメント](https://learn.microsoft.com/ja-jp/azure/ai-services/translator/overview)
- [Azure AI Speech ドキュメント](https://learn.microsoft.com/ja-jp/azure/ai-services/speech-service/overview)
- [Microsoft Agent Hackathon 2026](https://zenn.dev/hackathons/microsoft-agent-hackathon-2026)
