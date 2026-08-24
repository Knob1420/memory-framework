# 环境说明

主环境（必装）与可选环境（按职责装）互相独立——**不装 MinerU 不影响除 pdf/image 外的一切开发**。

## 主环境（所有人）

```bash
uv sync                                        # 全部 Python 依赖（含 markitdown）
cp .env.example .env                           # 填 LLM key（P0 可空）
uv run uvicorn memory.main:app --port 8000     # 起服务
uv run pytest                                  # 41 个测试全绿即环境 OK
```

## MinerU 环境（可选，负责人：mineru 模块开发者）

PDF/图片 OCR 的独立 conda 环境，主项目经**子进程**调用（无 import 依赖，天然隔离）。

```bash
# 1. 建环境并安装（CUDA 机器）
conda create -n mineru python=3.12 -y
conda run -n mineru pip install -U mineru

# 2. 验证（首次会下模型，几分钟）
conda run -n mineru mineru --version
conda run -n mineru mineru -p 任意.pdf -o /tmp/mineru_test -b hybrid-engine

# 3. 主项目 config.yaml 打开开关
#    mineru: { enabled: true, conda_env: mineru, gpu: "<卡号>" }
```

不装的表现：pdf/image 文档派生时记 `derived_state='failed'` + 原因，其余格式正常。
换服务器迁移：主环境 uv sync + 本节照抄，两件事互不阻塞。

### 本机现状

- 本机直接复用 conda env `memory`（torch 2.10+cu128 / mineru 3.4.4，rag-clean 验证过）
- 新服务器自建时照上文安装，装好后 config.yaml 的 `conda_env` 改成对应名字即可
- 踩坑记录（新服务器部署时参考）：
  - `mineru[all]` 的 torch cu130 依赖链不在阿里镜像（nvidia-*-cu13 轮子只在
    pypi.nvidia.com），建议先装 torch 再装 mineru[core]，或直接用本节官方命令
  - uv 装 vllm 大轮子偶发 mmap ENOMEM，换 pip 可绕

## 本地 embedding 服务（bge-m3，独立进程）

向量模型与 LLM 分端点部署（decisions.md）。bge-m3 ~568M 参数，fp16 推理占显存
**~1.6 GB**（实测 L40）；跑在一台 L40 的零头上，与 MinerU 互不冲突。

依赖（torch / FlagEmbedding / fastapi / uvicorn）都在 **conda env `memory`**——
与 MinerU 共用，已装好，无需额外安装。

```bash
# 启动（repo 根目录；GPU 卡号按需改）
CUDA_VISIBLE_DEVICES=0 conda run --no-capture-output -n memory \
    env PYTHONPATH=src uvicorn memory.llm.embed_server:app --host 127.0.0.1 --port 8001

# 验证
curl -s http://127.0.0.1:8001/health

# 主项目 .env 接入（注意 base_url 带 /v1，OpenAI SDK 在其后拼 /embeddings）
EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIM=1024
```

模型权重路径用环境变量 `EMBED_MODEL_PATH` 覆盖（默认本机 pretrain_model 下的 BAAI/bge-m3）。
注意：FlagEmbedding 新版 `encode()` 不再收 `normalize_embeddings`，服务内自行 L2 归一化。
