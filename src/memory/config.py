"""配置：yaml + .env 读取，全项目唯一的配置入口。"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()  # side effect：进程启动时读 .env

_CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "config.yaml"))


@dataclass
class Config:
    # LLM
    llm_api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))
    llm_base_url: str = field(
        default_factory=lambda: os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    )
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", ""))

    # embedding（维度建表时定死，换模型 = 重建向量表）
    embedding_model: str = field(default_factory=lambda: os.environ.get("EMBEDDING_MODEL", ""))
    embedding_base_url: str = field(
        default_factory=lambda: os.environ.get("EMBEDDING_BASE_URL", "")
    )  # 本地部署的 OpenAI 兼容端点；空则回落 llm_base_url
    embedding_dim: int = field(default_factory=lambda: int(os.environ.get("EMBEDDING_DIM", "1024")))

    # 路径
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", "data")))

    # search 截断阈值：content ≤200 字直接返全文，否则返摘要
    search_fulltext_chars: int = 200

    # workspace 注册表（编排门消费）。ponytail: P0 只有名单，P1 升级为组件注册 dict
    workspaces: list[str] = field(default_factory=list)

    # MinerU（PDF/图片 OCR，可选环境；未启用时 pdf/image 派生失败留痕）
    mineru_enabled: bool = False
    mineru_conda_env: str = "mineru"
    mineru_gpu: str = "2"
    mineru_backend: str = "hybrid-engine"
    mineru_effort: str = "medium"
    mineru_lang: str = "ch"

    # Phoenix 同步（拉型采集，REST 版）。url 为空则不启动。
    phoenix_url: str = field(default_factory=lambda: os.environ.get("PHOENIX_URL", ""))
    phoenix_project: str = "docgen-real-tc03"
    phoenix_workspace: str = "docgen"
    phoenix_interval_s: int = 300
    phoenix_start_from: str = "all"  # all=吃历史 | now=只收新的

    # 演化调度器（pending 池轮询周期）
    scheduler_interval_s: int = 10


def load_config() -> Config:
    """yaml 打底，环境变量优先。"""
    cfg = Config()
    if _CONFIG_PATH.exists():
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        if isinstance(raw.get("workspaces"), list):
            cfg.workspaces = raw["workspaces"]
        if isinstance(raw.get("phoenix"), dict):
            p = raw["phoenix"]
            cfg.phoenix_url = p.get("url", cfg.phoenix_url)
            cfg.phoenix_project = p.get("project", cfg.phoenix_project)
            cfg.phoenix_interval_s = int(p.get("interval_s", cfg.phoenix_interval_s))
            cfg.phoenix_start_from = str(p.get("start_from", cfg.phoenix_start_from))
        if isinstance(raw.get("mineru"), dict):
            m = raw["mineru"]
            cfg.mineru_enabled = bool(m.get("enabled", cfg.mineru_enabled))
            cfg.mineru_conda_env = str(m.get("conda_env", cfg.mineru_conda_env))
            cfg.mineru_gpu = str(m.get("gpu", cfg.mineru_gpu))
            cfg.mineru_backend = str(m.get("backend", cfg.mineru_backend))
            cfg.mineru_effort = str(m.get("effort", cfg.mineru_effort))
            cfg.mineru_lang = str(m.get("lang", cfg.mineru_lang))
    return cfg
