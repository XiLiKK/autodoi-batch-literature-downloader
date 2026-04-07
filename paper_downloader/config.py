"""配置加载模块"""

import os
from pathlib import Path
from typing import Any

import yaml


# 项目根目录：从当前文件位置向上两级
_DEFAULT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _DEFAULT_ROOT / "config" / "settings.yaml"


def load_config(config_path: str | Path | None = None, project_root: str | Path | None = None) -> dict[str, Any]:
    """加载 YAML 配置文件，合并默认值"""
    if config_path is None:
        config_path = _CONFIG_PATH
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # 确定项目根目录
    root = Path(project_root) if project_root else _DEFAULT_ROOT
    cfg["_root"] = str(root)

    # 解析相对路径为绝对路径
    cfg["download"] = cfg.get("download", {})
    cfg["download"]["output_dir"] = str(root / cfg["download"].get("output_dir", "output/downloads"))

    cfg["database"] = cfg.get("database", {})
    cfg["database"]["path"] = str(root / cfg["database"].get("path", "state/library.db"))

    cfg["logging"] = cfg.get("logging", {})
    cfg["logging"]["output_dir"] = str(root / cfg["logging"].get("output_dir", "output"))

    return cfg


def get_email(cfg: dict) -> str:
    """获取 API 邮箱，优先环境变量。可选：没有邮箱也能跑。"""
    env_email = os.environ.get("PAPER_DOWNLOADER_EMAIL")
    if env_email:
        return env_email
    api_cfg = cfg.get("api", {})
    email = api_cfg.get("email", "")
    if not email or email == "your_email@example.com":
        return ""  # 不强制，没邮箱也能跑
    return email
