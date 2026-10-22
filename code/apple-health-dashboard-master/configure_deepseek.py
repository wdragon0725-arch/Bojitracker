#!/usr/bin/env python3
"""Store a DeepSeek API key in a git-ignored .env outside the web root."""

from getpass import getpass
from pathlib import Path
import sys


APP_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = APP_DIR.parents[1]
ENV_PATH = WORKSPACE_DIR / ".env"
LEGACY_KEY_PATH = WORKSPACE_DIR / "data" / "deepseek.key"


def save_key(key: str) -> None:
    if not key.startswith("sk-") or len(key) < 20 or any(character in key for character in "\r\n\x00"):
        raise SystemExit("API Key 格式不正确，未保存。")
    lines = ENV_PATH.read_text(encoding="utf-8-sig").splitlines() if ENV_PATH.is_file() else []
    lines = [line for line in lines if line.strip().split("=", 1)[0].strip() != "DEEPSEEK_API_KEY"]
    lines.append(f"DEEPSEEK_API_KEY={key}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if LEGACY_KEY_PATH.is_file():
        LEGACY_KEY_PATH.unlink()


def main() -> None:
    if sys.argv[1:] == ["--migrate-legacy"]:
        if not LEGACY_KEY_PATH.is_file():
            raise SystemExit("没有找到旧版密钥文件，未迁移。")
        if ENV_PATH.is_file() and any(line.strip().startswith("DEEPSEEK_API_KEY=")
                                      for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines()):
            raise SystemExit(".env 已含 API Key，为避免覆盖，未迁移旧版密钥。")
        save_key(LEGACY_KEY_PATH.read_text(encoding="utf-8").strip())
        print("旧版密钥已迁移到项目根目录 .env，原密钥文件已移除。")
        return
    if len(sys.argv) > 1:
        raise SystemExit("不支持的参数。")
    print("配置 DeepSeek Chat Agent")
    print("API Key 只会保存在本机项目根目录 .env，不会写入网页源码。")
    key = getpass("请输入新的 DeepSeek API Key（输入内容不会显示）：").strip()
    save_key(key)
    print(f"配置完成：{ENV_PATH}")
    print("如果服务正在运行，请关闭后重新启动。")


if __name__ == "__main__":
    main()
