#!/bin/bash
set -e

# 2026년 공식 Notion MCP 이미지
IMAGE_NAME="mcp/notion"
CONTAINER_NAME="notion-mcp"

echo "🚚 공식 Notion MCP 이미지 불러오는 중..."
docker pull $IMAGE_NAME

echo "🔄 기존 컨테이너 정리..."
docker stop $CONTAINER_NAME 2>/dev/null || true
docker rm $CONTAINER_NAME 2>/dev/null || true

# 컨테이너 실행
# 공식 이미지는 NOTION_TOKEN 또는 NOTION_API_TOKEN 환경 변수를 사용합니다.
echo "🚀 Notion MCP 서버 실행..."
docker run -d \
  --name $CONTAINER_NAME \
  -i \
  --env-file .env \
  --restart always \
  $IMAGE_NAME

echo "✅ 배포 완료! 컨테이너 상태:"
docker ps -f name=$CONTAINER_NAME