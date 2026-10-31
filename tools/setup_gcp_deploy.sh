#!/usr/bin/env bash
# Cloud Run 자동 배포를 위한 GCP 1회 설정 스크립트 (키 파일 없는 Workload Identity 방식)
#
# 사용법:  bash tools/setup_gcp_deploy.sh
# 전제:    gcloud 로그인 완료(gcloud auth login), gh 로그인 완료(gh auth login)
# 여러 번 실행해도 안전합니다(이미 있는 항목은 건너뜀).
#
# 하는 일:
#   1. 필요한 API 활성화
#   2. 배포용 서비스 계정 github-deployer 생성 + 배포에 필요한 역할 부여
#   3. GitHub Actions가 키 없이 이 서비스 계정을 쓸 수 있도록 Workload Identity 풀/공급자 생성
#   4. GitHub 저장소 변수(GCP_PROJECT_ID, GCP_WIF_PROVIDER, GCP_DEPLOY_SA) 등록
#   5. 배포 워크플로 1회 실행
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-hscredit-ortool}"
REGION="${CLOUD_RUN_REGION:-asia-northeast3}"
GITHUB_REPO="${GITHUB_REPO:-ngryun/hscredit_ortool}"
SA_NAME="github-deployer"
POOL="github"
PROVIDER="github"

export CLOUDSDK_CORE_DISABLE_PROMPTS=1
# gcloud가 Python 3.9 경고를 매번 찍으므로 숨김
g() { gcloud "$@" 2> >(grep -v -E "WARNING:|Python 3\.|gcloud components|CLOUDSDK_PYTHON|reinstall|compatible Python|version 3.10|prompt to install|^$" >&2 || true); }

echo "== 프로젝트: $PROJECT_ID / 리전: $REGION / 저장소: $GITHUB_REPO"
PROJECT_NUMBER=$(g projects describe "$PROJECT_ID" --format="value(projectNumber)")
SA_EMAIL="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
COMPUTE_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

echo "== 1/5 API 활성화"
g services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com --project "$PROJECT_ID"

echo "== 2/5 배포용 서비스 계정"
if ! g iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
  g iam service-accounts create "$SA_NAME" --display-name "GitHub Actions deployer" --project "$PROJECT_ID"
fi
for ROLE in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.writer \
            roles/storage.admin roles/iam.serviceAccountUser roles/serviceusage.serviceUsageConsumer \
            roles/logging.viewer; do
  g projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$SA_EMAIL" \
    --role "$ROLE" --condition=None >/dev/null
  echo "   역할 부여: $ROLE"
done
# 소스 배포 시 Cloud Build가 기본 컴퓨트 서비스 계정으로 실행되므로 빌드 권한 필요
g projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$COMPUTE_SA" \
  --role roles/cloudbuild.builds.builder --condition=None >/dev/null
echo "   역할 부여(빌드 실행 계정): roles/cloudbuild.builds.builder"
# 소스 배포가 이미지를 넣는 저장소를 미리 생성 (없으면 배포 계정에 생성 권한이 더 필요함)
g artifacts repositories describe cloud-run-source-deploy --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1 || \
  g artifacts repositories create cloud-run-source-deploy --repository-format docker \
    --location "$REGION" --project "$PROJECT_ID"

echo "== 3/5 Workload Identity (키 없는 GitHub 인증)"
if ! g iam workload-identity-pools describe "$POOL" --location global --project "$PROJECT_ID" >/dev/null 2>&1; then
  g iam workload-identity-pools create "$POOL" --location global --display-name "GitHub Actions" --project "$PROJECT_ID"
fi
if ! g iam workload-identity-pools providers describe "$PROVIDER" --location global \
     --workload-identity-pool "$POOL" --project "$PROJECT_ID" >/dev/null 2>&1; then
  g iam workload-identity-pools providers create-oidc "$PROVIDER" --location global \
    --workload-identity-pool "$POOL" --display-name "GitHub" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition "assertion.repository=='$GITHUB_REPO'" \
    --project "$PROJECT_ID"
fi
WIF_PROVIDER="projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/providers/$PROVIDER"
g iam service-accounts add-iam-policy-binding "$SA_EMAIL" --project "$PROJECT_ID" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.repository/$GITHUB_REPO" >/dev/null
echo "   공급자: $WIF_PROVIDER"

echo "== 4/5 GitHub 저장소 변수 등록"
gh variable set GCP_PROJECT_ID --repo "$GITHUB_REPO" --body "$PROJECT_ID"
gh variable set GCP_WIF_PROVIDER --repo "$GITHUB_REPO" --body "$WIF_PROVIDER"
gh variable set GCP_DEPLOY_SA --repo "$GITHUB_REPO" --body "$SA_EMAIL"
gh variable set CLOUD_RUN_REGION --repo "$GITHUB_REPO" --body "$REGION"

echo "== 5/5 배포 워크플로 실행"
gh workflow run deploy-cloud-run.yml --repo "$GITHUB_REPO" --ref main
echo
echo "완료. 진행 상황: https://github.com/$GITHUB_REPO/actions/workflows/deploy-cloud-run.yml"
echo "배포가 끝나면 Actions 로그 마지막 줄에 서비스 URL이 표시됩니다."
