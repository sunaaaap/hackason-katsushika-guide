#!/bin/bash
# =============================================================
# 葛飾区行政窓口サポートエージェント — Azure リソース作成スクリプト
# =============================================================
# 前提条件:
#   - Azure CLI インストール済み (brew install azure-cli)
#   - az login 実行済み
#
# 実行方法:
#   chmod +x setup_azure.sh
#   ./setup_azure.sh
# =============================================================

set -e  # エラー時に即座に停止

# ---------------------------------------------------------------
# 変数定義（必要に応じて変更してください）
# ---------------------------------------------------------------
RESOURCE_GROUP="rg-katsushika-agent"
LOCATION="japaneast"
LOCATION_OAI="eastus"          # Azure OpenAI は eastus が安定

OAI_NAME="oai-katsushika-agent"
SEARCH_NAME="srch-katsushika-agent"
STORAGE_NAME="stkatsushikaagt"  # ストレージ名は小文字英数字24文字以内
CONTAINER_NAME="documents"
SPEECH_NAME="speech-katsushika-agent"
TRANSLATOR_NAME="translator-katsushika-agent"
FUNC_NAME="func-katsushika-agent"
WEBAPP_NAME="stapp-katsushika-agent"

echo "=============================================="
echo " Azure リソース作成を開始します"
echo " リソースグループ: $RESOURCE_GROUP"
echo " リージョン: $LOCATION"
echo "=============================================="

# ---------------------------------------------------------------
# 1. リソースグループ
# ---------------------------------------------------------------
echo ""
echo "[1/8] リソースグループを作成中..."
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --output table

echo "✅ リソースグループ作成完了"

# ---------------------------------------------------------------
# 2. Azure OpenAI
# ---------------------------------------------------------------
echo ""
echo "[2/8] Azure OpenAI リソースを作成中..."
az cognitiveservices account create \
  --name "$OAI_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --kind OpenAI \
  --sku S0 \
  --location "$LOCATION_OAI" \
  --yes \
  --output table

echo "  → GPT-4o モデルをデプロイ中..."
az cognitiveservices account deployment create \
  --name "$OAI_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --deployment-name "gpt-4o" \
  --model-name "gpt-4o" \
  --model-version "2024-11-20" \
  --model-format OpenAI \
  --sku-capacity 10 \
  --sku-name "Standard"

echo "  → text-embedding-3-small モデルをデプロイ中..."
az cognitiveservices account deployment create \
  --name "$OAI_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --deployment-name "text-embedding-3-small" \
  --model-name "text-embedding-3-small" \
  --model-version "1" \
  --model-format OpenAI \
  --sku-capacity 10 \
  --sku-name "Standard"

echo "✅ Azure OpenAI 作成完了"

# ---------------------------------------------------------------
# 3. Azure AI Search
# ---------------------------------------------------------------
echo ""
echo "[3/8] Azure AI Search を作成中..."
az search service create \
  --name "$SEARCH_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --sku free \
  --location "$LOCATION" \
  --output table

echo "✅ Azure AI Search 作成完了"

# ---------------------------------------------------------------
# 4. Azure Blob Storage
# ---------------------------------------------------------------
echo ""
echo "[4/8] Azure Blob Storage を作成中..."
az storage account create \
  --name "$STORAGE_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku Standard_LRS \
  --output table

STORAGE_KEY=$(az storage account keys list \
  --account-name "$STORAGE_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "[0].value" --output tsv)

az storage container create \
  --name "$CONTAINER_NAME" \
  --account-name "$STORAGE_NAME" \
  --account-key "$STORAGE_KEY"

echo "✅ Azure Blob Storage 作成完了（コンテナ: $CONTAINER_NAME）"

# ---------------------------------------------------------------
# 5. Azure AI Speech
# ---------------------------------------------------------------
echo ""
echo "[5/8] Azure AI Speech を作成中..."
az cognitiveservices account create \
  --name "$SPEECH_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --kind SpeechServices \
  --sku F0 \
  --location "$LOCATION" \
  --yes \
  --output table

echo "✅ Azure AI Speech 作成完了（Free F0）"

# ---------------------------------------------------------------
# 6. Azure AI Translator
# ---------------------------------------------------------------
echo ""
echo "[6/8] Azure AI Translator を作成中..."
az cognitiveservices account create \
  --name "$TRANSLATOR_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --kind TextTranslation \
  --sku F0 \
  --location "global" \
  --yes \
  --output table

echo "✅ Azure AI Translator 作成完了（Free F0）"

# ---------------------------------------------------------------
# 7. Azure Functions
# ---------------------------------------------------------------
echo ""
echo "[7/8] Azure Functions を作成中..."
az functionapp create \
  --name "$FUNC_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --consumption-plan-location "$LOCATION" \
  --runtime python \
  --runtime-version "3.11" \
  --functions-version 4 \
  --storage-account "$STORAGE_NAME" \
  --os-type linux \
  --output table

echo "✅ Azure Functions 作成完了"

# ---------------------------------------------------------------
# 8. エンドポイント・キーを取得して .env に出力
# ---------------------------------------------------------------
echo ""
echo "[8/8] 接続情報を収集中..."

OAI_ENDPOINT=$(az cognitiveservices account show \
  --name "$OAI_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "properties.endpoint" --output tsv)

OAI_KEY=$(az cognitiveservices account keys list \
  --name "$OAI_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "key1" --output tsv)

SEARCH_ENDPOINT="https://${SEARCH_NAME}.search.windows.net"
SEARCH_KEY=$(az search admin-key show \
  --service-name "$SEARCH_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "primaryKey" --output tsv)

STORAGE_CONN=$(az storage account show-connection-string \
  --name "$STORAGE_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "connectionString" --output tsv)

SPEECH_KEY=$(az cognitiveservices account keys list \
  --name "$SPEECH_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "key1" --output tsv)
SPEECH_REGION="$LOCATION"

TRANSLATOR_KEY=$(az cognitiveservices account keys list \
  --name "$TRANSLATOR_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "key1" --output tsv)
TRANSLATOR_ENDPOINT="https://api.cognitive.microsofttranslator.com"

# .env ファイルに書き出し
cat > .env << EOF
# ============================================================
# Azure 接続情報 — 自動生成 $(date '+%Y-%m-%d %H:%M')
# ⚠️  このファイルを Git にコミットしないこと！.gitignore に追加すること
# ============================================================

# Azure OpenAI
AZURE_OPENAI_ENDPOINT=${OAI_ENDPOINT}
AZURE_OPENAI_KEY=${OAI_KEY}
AZURE_OPENAI_DEPLOYMENT_GPT4O=gpt-4o
AZURE_OPENAI_DEPLOYMENT_EMBEDDING=text-embedding-3-small
AZURE_OPENAI_API_VERSION=2024-10-21

# Azure AI Search
AZURE_SEARCH_ENDPOINT=${SEARCH_ENDPOINT}
AZURE_SEARCH_KEY=${SEARCH_KEY}
AZURE_SEARCH_INDEX_NAME=katsushika-procedures

# Azure Blob Storage
AZURE_STORAGE_CONNECTION_STRING=${STORAGE_CONN}
AZURE_STORAGE_CONTAINER=${CONTAINER_NAME}

# Azure AI Speech
AZURE_SPEECH_KEY=${SPEECH_KEY}
AZURE_SPEECH_REGION=${SPEECH_REGION}

# Azure AI Translator
AZURE_TRANSLATOR_KEY=${TRANSLATOR_KEY}
AZURE_TRANSLATOR_ENDPOINT=${TRANSLATOR_ENDPOINT}
AZURE_TRANSLATOR_REGION=${LOCATION}
EOF

echo "✅ .env ファイルを生成しました"

# ---------------------------------------------------------------
# 完了サマリー
# ---------------------------------------------------------------
echo ""
echo "=============================================="
echo " ✅ 全リソースの作成が完了しました！"
echo "=============================================="
echo ""
echo " 次のステップ:"
echo "   1. python upload_and_index.py  ← データをアップロード＆インデックス作成"
echo "   2. Azure Functions の API を実装"
echo "   3. フロントエンドを Static Web Apps にデプロイ"
echo ""
echo " リソース一覧を確認:"
echo "   az resource list --resource-group $RESOURCE_GROUP --output table"
echo "=============================================="
