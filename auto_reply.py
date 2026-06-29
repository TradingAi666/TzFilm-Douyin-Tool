#!/usr/bin/env python3
"""
抖音评论自动回复 — Python 封装

封装了 auto-reply/ 目录下的 Node.js Playwright 脚本，
用一行命令完成评论导出和批量回复。

用法:
    python3 auto_reply.py export "视频标题关键词"     # 导出未回复评论
    python3 auto_reply.py reply "视频标题关键词"      # 发送 AI 生成的回复
    python3 auto_reply.py setup                       # 首次安装

前置:
    - Node.js >= 18
    - Chrome 浏览器
    - 已登录 creator.douyin.com
"""

import subprocess
import sys
import os
import json
from pathlib import Path
from shutil import which

AUTO_REPLY_DIR = Path(__file__).parent / "auto-reply"
CODEX_RUNTIME_DIR = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies"
CODEX_NODE = CODEX_RUNTIME_DIR / "node/bin/node"
CODEX_PNPM = CODEX_RUNTIME_DIR / "bin/pnpm"


def _resolve_binary(env_key, command, fallback=None):
    configured = os.environ.get(env_key)
    if configured and Path(configured).exists():
        return configured
    found = which(command)
    if found:
        return found
    if fallback and fallback.exists():
        return str(fallback)
    return None


NODE_BIN = _resolve_binary("NODE_BINARY", "node", CODEX_NODE)
PNPM_BIN = _resolve_binary("PNPM_BINARY", "pnpm", CODEX_PNPM)


def _node_env() -> dict:
    env = os.environ.copy()
    path_parts = []
    if CODEX_NODE.exists():
        path_parts.append(str(CODEX_NODE.parent))
    if CODEX_PNPM.exists():
        path_parts.append(str(CODEX_PNPM.parent))
    path_parts.append(env.get("PATH", ""))
    env["PATH"] = os.pathsep.join(path_parts)
    return env


def _check_node():
    """检查 Node.js 是否可用"""
    if not NODE_BIN:
        print("❌ 未找到 Node.js，请先安装 Node.js，或设置 NODE_BINARY 指向 node 可执行文件")
        sys.exit(1)
    subprocess.run([NODE_BIN, "--version"], capture_output=True, check=True)


def _pnpm(*args) -> subprocess.CompletedProcess:
    """在 auto-reply 目录下执行 pnpm 命令"""
    if not PNPM_BIN:
        print("❌ 未找到 pnpm，请安装 pnpm，或设置 PNPM_BINARY 指向 pnpm 可执行文件")
        sys.exit(1)
    return subprocess.run(
        [PNPM_BIN] + list(args),
        cwd=str(AUTO_REPLY_DIR),
        env=_node_env(),
        check=False,
    )


def _run_script(script: str, *args, timeout=600) -> subprocess.CompletedProcess:
    """运行 Node.js 脚本"""
    cmd = [NODE_BIN, f"src/{script}"] + list(args)
    return subprocess.run(
        cmd,
        cwd=str(AUTO_REPLY_DIR),
        env=_node_env(),
        timeout=timeout,
        check=False,
    )


def cmd_setup():
    """首次安装：装依赖 + 登录"""
    _check_node()

    print("📦 安装 Node.js 依赖...")
    result = _pnpm("install")
    if result.returncode != 0:
        print("❌ pnpm install 失败，请检查网络或手动进入 auto-reply/ 目录执行")
        sys.exit(1)

    print("🌐 安装 Playwright 浏览器...")
    _pnpm("exec", "playwright", "install", "chromium")

    print("\n🔑 接下来会打开浏览器，请在浏览器中登录抖音创作者后台，")
    print("   登录完成后回终端按回车保存登录态。\n")
    input("   按回车开始...")

    _run_script("auth-douyin.mjs", timeout=None)
    print("\n✅ 设置完成！现在可以用 export / reply 命令了。")


def cmd_export(title: str, limit: int = 300):
    """导出指定视频的未回复评论"""
    _check_node()

    print(f"🔍 扫描视频 «{title}» 的评论...")
    result = _run_script(
        "export-douyin-comments.mjs",
        title,
        "--limit", str(limit),
        "--timeout", "120000",
    )

    if result.returncode != 0:
        print("❌ 导出失败，可能是标题没匹配或登录已过期")
        print("   试试换一个更长的关键词，或重新运行 python3 auto_reply.py setup")
        sys.exit(1)

    # 读取结果
    output_file = AUTO_REPLY_DIR / "comments-output" / "unreplied-comments.json"
    if output_file.exists():
        with open(output_file, encoding="utf-8") as f:
            data = json.load(f)
        comments = data.get("comments", [])
        work = data.get("selectedWork", {})
        print(f"\n✅ 导出成功！")
        print(f"   作品: {work.get('title', title)}")
        print(f"   评论: {len(comments)} 条")
        print(f"\n   AI 生成回复后，保存到 auto-reply/comments-output/auto-reply-plan.json")
        print(f"   然后运行: python3 auto_reply.py reply \"{title}\"")
    else:
        print("⚠️ 导出完成但找不到输出文件")


def cmd_reply(title: str, limit: int = 30, dry_run: bool = False):
    """执行批量回复"""
    _check_node()

    plan_file = AUTO_REPLY_DIR / "comments-output" / "auto-reply-plan.json"
    if not plan_file.exists():
        print("❌ 未找到回复计划文件")
        print(f"   请先创建: {plan_file}")
        print("   格式: { \"selectedWork\": {...}, \"comments\": [{\"username\":\"...\", \"commentText\":\"...\", \"replyMessage\":\"...\"}] }")
        sys.exit(1)

    with open(plan_file, encoding="utf-8") as f:
        plan = json.load(f)

    total = len(plan.get("comments", []))
    print(f"📨 准备回复 {total} 条评论...")

    args = [
        "--limit", str(limit),
        "--timeout", "600000",
        str(plan_file),
    ]
    if dry_run:
        args.insert(0, "--dry-run")
        print("   🔍 试运行模式（不会真正发送）")

    result = _run_script("reply-douyin-comments.mjs", *args)

    if result.returncode != 0:
        print("❌ 回复执行失败")
        sys.exit(1)

    # 读取结果
    result_file = AUTO_REPLY_DIR / "comments-output" / "reply-comments-result.json"
    if result_file.exists():
        with open(result_file, encoding="utf-8") as f:
            res = json.load(f)
        results = res.get("results", [])
        replied = sum(1 for r in results if r.get("status") == "replied")
        print(f"\n✅ 本轮发送: {replied} 条")
        if replied < total:
            print(f"   剩余: {total - replied} 条（不在当前页面，再次运行继续发送）")
    else:
        print("✅ 回复完成")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("命令:")
        print("  setup                      首次安装")
        print("  export <标题关键词>         导出未回复评论")
        print("  reply  <标题关键词>         执行批量回复")
        print("\n示例:")
        print("  python3 auto_reply.py setup")
        print("  python3 auto_reply.py export \"API中转站\"")
        print("  python3 auto_reply.py reply \"API中转站\"")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "setup":
        cmd_setup()
    elif cmd == "export":
        if len(sys.argv) < 3:
            print("用法: python3 auto_reply.py export <标题关键词>")
            sys.exit(1)
        cmd_export(sys.argv[2])
    elif cmd == "reply":
        if len(sys.argv) < 3:
            print("用法: python3 auto_reply.py reply <标题关键词>")
            sys.exit(1)
        # 支持 --dry-run 和 --limit
        dry_run = "--dry-run" in sys.argv
        limit = 30
        for i, arg in enumerate(sys.argv):
            if arg == "--limit" and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])
        cmd_reply(sys.argv[2], limit=limit, dry_run=dry_run)
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
