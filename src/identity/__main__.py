"""身份基底 CLI — python -m src.identity 入口。"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from config.settings import DATA_DIR


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m src.identity",
        description="身份基底 CLI / Identity substrate CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # scan — 干跑，显示会创建/更新/弃用哪些块
    scan_p = sub.add_parser("scan", help="Dry-run: 显示将发生的变更 (不写 DB)")
    scan_p.add_argument(
        "--dir",
        default=str(DATA_DIR / "identity"),
        help="身份文件目录 (默认: data/identity/)",
    )

    # rebuild — 写入 DB
    rebuild_p = sub.add_parser("rebuild", help="从 Markdown 重建主张到 DB")
    rebuild_p.add_argument(
        "--confirm",
        action="store_true",
        help="确认写入；不加此参数时与 scan 相同",
    )
    rebuild_p.add_argument(
        "--dir",
        default=str(DATA_DIR / "identity"),
        help="身份文件目录 (默认: data/identity/)",
    )

    # validate — 一致性检查
    validate_p = sub.add_parser("validate", help="校验身份文件一致性")
    validate_p.add_argument(
        "--strict",
        action="store_true",
        help="严格模式：要求每个块都有显式 ID",
    )
    validate_p.add_argument(
        "--dir",
        default=str(DATA_DIR / "identity"),
        help="身份文件目录 (默认: data/identity/)",
    )

    # show — 显示单条主张及修订历史
    show_p = sub.add_parser("show", help="显示主张详情及修订历史")
    show_p.add_argument("claim_id", help="主张 ID")

    # cache-stats — 缓存统计
    sub.add_parser("cache-stats", help="显示 LLM 提取缓存统计")

    # cache-clear — 清空缓存
    cc_p = sub.add_parser("cache-clear", help="清空 LLM 提取缓存")
    cc_p.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计条目，不删除",
    )
    cc_p.add_argument(
        "--confirm",
        action="store_true",
        help="确认删除",
    )
    cc_p.add_argument(
        "--scope",
        choices=["file", "claim", "all"],
        default="all",
        help="清空范围：all=全部, file=按文件名前缀, claim=按 claim 前缀",
    )
    cc_p.add_argument(
        "--source",
        default=None,
        help="--scope file/claim 时，指定文件名或 claim_id 前缀",
    )

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    asyncio.run(_dispatch(args))


# ---------------------------------------------------------------------------
# 调度
# ---------------------------------------------------------------------------

async def _dispatch(args: argparse.Namespace) -> None:
    from src.identity.store import IdentityStore
    from src.identity.auth import create_system_auth

    db_path = DATA_DIR / "identity.db"
    store = IdentityStore(db_path=db_path)
    await store.init()

    try:
        if args.command == "scan":
            await _cmd_scan(store, args)
        elif args.command == "rebuild":
            await _cmd_rebuild(store, args)
        elif args.command == "validate":
            await _cmd_validate(store, args)
        elif args.command == "show":
            await _cmd_show(store, args)
        elif args.command == "cache-stats":
            await _cmd_cache_stats(store, args)
        elif args.command == "cache-clear":
            await _cmd_cache_clear(store, args)
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# scan — 干跑：显示差异
# ---------------------------------------------------------------------------

async def _cmd_scan(store, args: argparse.Namespace) -> None:
    from src.identity.parser import IdentityParser
    from src.identity.auth import create_system_auth

    identity_dir = Path(args.dir)
    if not identity_dir.exists():
        print(f"[scan] 目录不存在: {identity_dir}")
        sys.exit(1)

    auth = create_system_auth()
    parser = IdentityParser(store=store, identity_dir=identity_dir)

    # 扫描所有 .md 文件，统计可解析的块数
    md_files = sorted(identity_dir.rglob("*.md"))
    if not md_files:
        print(f"[scan] {identity_dir} 中没有 .md 文件")
        return

    print(f"[scan] 扫描目录: {identity_dir}")
    print(f"[scan] 找到 {len(md_files)} 个 .md 文件\n")

    total_blocks = 0
    for md_path in md_files:
        try:
            content = md_path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  [!] 无法读取 {md_path.name}: {e}")
            continue

        rel = str(md_path.relative_to(identity_dir))
        blocks = parser.parse_text(content, rel)
        total_blocks += len(blocks)
        print(f"  {rel}: {len(blocks)} 个块")
        for blk in blocks:
            tag = f"[id={blk.stable_block_key[:8]}]" if blk.stable_block_key else "[无 ID]"
            snippet = blk.text[:60].replace("\n", " ").strip()
            print(f"    {tag} {snippet!r}")

    print(f"\n[scan] 合计: {total_blocks} 个块（仅解析，未写入 DB）")

    # 与现有主张比较
    existing = await store.list_claims(auth)
    print(f"[scan] DB 中现有主张: {len(existing)} 条")


# ---------------------------------------------------------------------------
# rebuild — 写入 DB
# ---------------------------------------------------------------------------

async def _cmd_rebuild(store, args: argparse.Namespace) -> None:
    from src.identity.parser import IdentityParser
    from src.identity.auth import create_system_auth

    identity_dir = Path(args.dir)
    if not identity_dir.exists():
        print(f"[rebuild] 目录不存在: {identity_dir}")
        sys.exit(1)

    if not args.confirm:
        # 无 --confirm 时，执行干跑
        print("[rebuild] 未指定 --confirm，执行干跑（等同于 scan）\n")
        # 复用 scan 逻辑
        class _FakeArgs:
            dir = args.dir
        await _cmd_scan(store, _FakeArgs())
        return

    auth = create_system_auth()
    parser = IdentityParser(store=store, identity_dir=identity_dir)

    print(f"[rebuild] 开始重建: {identity_dir}")
    report = await parser.rebuild(auth)

    print(f"[rebuild] 完成:")
    print(f"  created    = {report.created}")
    print(f"  updated    = {report.updated}")
    print(f"  deprecated = {report.deprecated}")
    print(f"  skipped    = {report.skipped_tombstoned}")
    if report.errors:
        print(f"  errors ({len(report.errors)}):")
        for err in report.errors:
            print(f"    [!] {err}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# validate — 一致性检查
# ---------------------------------------------------------------------------

async def _cmd_validate(store, args: argparse.Namespace) -> None:
    from src.identity.parser import IdentityParser

    identity_dir = Path(args.dir)
    if not identity_dir.exists():
        print(f"[validate] 目录不存在: {identity_dir}")
        sys.exit(1)

    parser = IdentityParser(store=store, identity_dir=identity_dir)

    # 收集生产文件列表（所有 .md 文件的相对路径）
    md_files = sorted(identity_dir.rglob("*.md"))
    prod_files = [str(p.relative_to(identity_dir)) for p in md_files]

    print(f"[validate] 校验目录: {identity_dir} (strict={args.strict})")
    result = parser.validate(strict=args.strict, production_files=prod_files)

    if result.warnings:
        print(f"\n警告 ({len(result.warnings)}):")
        for w in result.warnings:
            print(f"  [W] {w}")

    if result.errors:
        print(f"\n错误 ({len(result.errors)}):")
        for e in result.errors:
            print(f"  [E] {e}")

    if result.passed:
        print("\n[validate] 校验通过 ✓")
    else:
        print("\n[validate] 校验失败 ✗")
        sys.exit(1)


# ---------------------------------------------------------------------------
# show — 显示主张详情
# ---------------------------------------------------------------------------

async def _cmd_show(store, args: argparse.Namespace) -> None:
    from src.identity.auth import create_system_auth

    auth = create_system_auth()
    claim = await store.get_claim(args.claim_id, auth)

    if claim is None:
        print(f"[show] 未找到主张: {args.claim_id}")
        sys.exit(1)

    print(f"claim_id  : {claim.claim_id}")
    print(f"status    : {claim.status}")
    print(f"label     : {claim.label}")
    print(f"category  : {claim.category}")
    print(f"text      : {claim.text}")
    print(f"source    : {claim.source_file}")
    print(f"created   : {claim.created_at}")
    print(f"updated   : {claim.updated_at}")

    # 修订历史
    revisions = await store.get_revisions(args.claim_id, auth)
    if revisions:
        print(f"\n修订历史 ({len(revisions)} 条):")
        for rev in revisions:
            print(f"  [{rev.created_at}] {rev.event_type} — {rev.text[:80]!r}")
    else:
        print("\n（无修订记录）")


# ---------------------------------------------------------------------------
# cache-stats — 缓存统计
# ---------------------------------------------------------------------------

async def _cmd_cache_stats(store, args: argparse.Namespace) -> None:
    cursor = await store._db.execute(
        "SELECT COUNT(*) FROM identity_extraction_cache"
    )
    row = await cursor.fetchone()
    count = row[0] if row else 0

    print(f"[cache-stats] identity_extraction_cache 条目数: {count}")

    # 最新几条
    if count > 0:
        cursor2 = await store._db.execute(
            "SELECT cache_key, created_at FROM identity_extraction_cache "
            "ORDER BY created_at DESC LIMIT 5"
        )
        rows = await cursor2.fetchall()
        print("  最近 5 条:")
        for r in rows:
            print(f"    {r[1]}  {r[0][:60]}")


# ---------------------------------------------------------------------------
# cache-clear — 清空缓存
# ---------------------------------------------------------------------------

async def _cmd_cache_clear(store, args: argparse.Namespace) -> None:
    from src.identity.auth import create_system_auth

    # 干跑：只统计
    if args.dry_run:
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM identity_extraction_cache"
        )
        row = await cursor.fetchone()
        count = row[0] if row else 0
        print(f"[cache-clear] dry-run: 将删除 {count} 条条目（scope={args.scope}）")
        return

    if not args.confirm:
        print("[cache-clear] 请添加 --confirm 以确认删除，或加 --dry-run 查看数量")
        sys.exit(1)

    auth = create_system_auth()
    source = getattr(args, "source", None)
    deleted = await store.clear_extraction_cache(args.scope, source, auth)
    print(f"[cache-clear] 已删除 {deleted} 条缓存条目 (scope={args.scope})")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
