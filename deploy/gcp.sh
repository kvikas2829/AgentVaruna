#!/usr/bin/env bash
# Deploys the Varuna demo to Google Cloud Run: the app (public HTTPS URL) and Qwen3 14B on an L4 GPU that scales to zero
# (private; only the app can call it). Safe to re-run: existing resources are reused and services are updated.
#
#   PROJECT_ID=varuna-demo-1234 BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX deploy/gcp.sh
#
# Optional: REGION for the services (default europe-west4, where new projects get Cloud Run L4 GPU quota; us-central1
# had none), IMAGE_REGION for the image repository (default us-central1), GCLOUD_CONFIG (default varuna-demo),
# SKIP_MODEL_BUILD=1 to reuse the model image.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-europe-west4}"
IMAGE_REGION="${IMAGE_REGION:-us-central1}"
export CLOUDSDK_ACTIVE_CONFIG_NAME="${GCLOUD_CONFIG:-varuna-demo}"
REPO="$IMAGE_REGION-docker.pkg.dev/$PROJECT_ID/varuna"
MODEL_IMAGE="$REPO/ollama-qwen3-14b:0.40.16"
APP_IMAGE="$REPO/varuna-app:$(git rev-parse --short HEAD)"
APP_SA="varuna-app@$PROJECT_ID.iam.gserviceaccount.com"

step() { printf '\n== %s\n' "$*"; }

step "Project $PROJECT_ID"
if ! gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud projects create "$PROJECT_ID" --name="Varuna demo"
fi
gcloud config set project "$PROJECT_ID" >/dev/null
if [ -n "${BILLING_ACCOUNT:-}" ] && [ "$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)')" != "True" ]; then
  gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT" >/dev/null
fi

step "APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com iam.googleapis.com

step "Image repository"
gcloud artifacts repositories describe varuna --location="$IMAGE_REGION" >/dev/null 2>&1 ||
  gcloud artifacts repositories create varuna --repository-format=docker --location="$IMAGE_REGION"

if [ -z "${SKIP_MODEL_BUILD:-}" ]; then
  step "Model image (Qwen3 14B built in, ~10 GB; takes a while)"
  gcloud builds submit deploy/ollama --tag="$MODEL_IMAGE" --machine-type=e2-highcpu-8 --disk-size=100 --timeout=3600s
fi

step "App image"
gcloud builds submit . --tag="$APP_IMAGE" --timeout=1800s

step "Model service (private, L4 GPU, scales to zero)"
gcloud run deploy varuna-model --image="$MODEL_IMAGE" --region="$REGION" \
  --gpu=1 --gpu-type=nvidia-l4 --no-gpu-zonal-redundancy --cpu=8 --memory=32Gi \
  --min-instances=0 --max-instances=1 --concurrency=4 --timeout=600 --no-cpu-throttling \
  --port=8080 --no-allow-unauthenticated
MODEL_URL="$(gcloud run services describe varuna-model --region="$REGION" --format='value(status.url)')"

step "App service account"
gcloud iam service-accounts describe "$APP_SA" >/dev/null 2>&1 ||
  gcloud iam service-accounts create varuna-app --display-name="Varuna app"
gcloud run services add-iam-policy-binding varuna-model --region="$REGION" \
  --member="serviceAccount:$APP_SA" --role=roles/run.invoker >/dev/null

step "App service (public)"
ENV="VARUNA_DATA_DIR=/tmp/varuna,VARUNA_MODEL_AUTH=gcp"
ENV="$ENV,VARUNA_AGENT_MODEL=qwen3:14b,VARUNA_AGENT_URL=$MODEL_URL/v1,VARUNA_AGENT_TIMEOUT=240"
ENV="$ENV,VARUNA_JUDGE=local,VARUNA_LOCAL_MODEL=qwen3:14b,VARUNA_LOCAL_URL=$MODEL_URL/v1,VARUNA_LOCAL_REASONING=none,VARUNA_JUDGE_TIMEOUT=30"
if gcloud run services describe varuna-app --region="$REGION" >/dev/null 2>&1; then
  ENV_FLAG="--update-env-vars=$ENV"                                   # keeps the existing token-signing secret
else
  ENV_FLAG="--set-env-vars=$ENV,VARUNA_SECRET=$(openssl rand -hex 24)"
fi
gcloud run deploy varuna-app --image="$APP_IMAGE" --region="$REGION" --service-account="$APP_SA" \
  --cpu=1 --memory=2Gi --min-instances=1 --max-instances=1 --no-cpu-throttling --timeout=3600 \
  --allow-unauthenticated "$ENV_FLAG"
APP_URL="$(gcloud run services describe varuna-app --region="$REGION" --format='value(status.url)')"
gcloud run services update varuna-app --region="$REGION" --update-env-vars="VARUNA_PUBLIC_URL=$APP_URL" >/dev/null

step "Done"
echo "App:   $APP_URL"
echo "Model: $MODEL_URL (private)"
