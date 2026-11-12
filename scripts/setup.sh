#!/bin/bash
# 一键初始化脚本
set -e

echo "=== 新闻聚合系统初始化 ==="

# 检查 Python
python3 --version || { echo "需要 Python 3.11+"; exit 1; }

# 检查 Docker
docker --version || { echo "需要安装 Docker Desktop"; exit 1; }

# 复制环境变量
if [ ! -f .env ]; then
  cp .env.example .env
  echo "✅ .env 文件已创建，请填写 API keys"
fi

# 安装依赖
echo "安装 Python 依赖..."
pip3 install -r requirements.txt

# 启动数据库
echo "启动 PostgreSQL + Redis..."
docker-compose up -d
echo "等待数据库就绪..."
sleep 5

# 测试采集器（不需要DB）
echo "测试数据采集..."
python3 scripts/test_collectors.py

echo ""
echo "=== 初始化完成 ==="
echo "下一步:"
echo "  1. 编辑 .env 文件，填入 API keys"
echo "  2. python3 main.py  启动主程序"
