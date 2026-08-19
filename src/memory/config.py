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
    embedding_dim: int = field(default_factory=lambda: int(os.environ.get("EMBEDDING_DIM", "1024")))

    # 路径
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", "data")))

    # search 截断阈值：content ≤200 字直接返全文，否则返摘要
    search_fulltext_chars: int = 200

    # workspace 注册表（编排门消费）。ponytail: P0 只有名单，P1 升级为组件注册 dict
    workspaces: list[str] = field(default_factory=list)


def load_config() -> Config:
    """yaml 打底，环境变量优先。"""
    cfg = Config()
    if _CONFIG_PATH.exists():
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        if isinstance(raw.get("workspaces"), list):
            cfg.workspaces = raw["workspaces"]
    return cfg
