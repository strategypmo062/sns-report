#!/usr/bin/env python3
"""
SNS 데이터 수집 CLI.

Usage:
  python3 src/run_collect_sns.py \\
    --keywords "LINE Premium,LINE 收費" \\
    --date-from 2026-03-25 --date-to 2026-04-02 \\
    --platforms ptt,dcard,threads \\
    --output data/collected_20260402.txt \\
    --max-posts 30 \\
    --ptt-boards Gossiping,MobileComm
"""

from __future__ import annotations

import argparse
import sys
import os
import time
from datetime import date
from pathlib import Path

# Allow imports from src/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env_loader import load_env_file as load_env
from collectors import get_collector, available_platforms
from collectors.text_formatter import format_all


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SNS 데이터 자동 수집")

    parser.add_argument(
        "--keywords",
        required=True,
        help="검색 키워드 (쉼표 구분). 예: 'LINE Premium,LINE 收費'",
    )
    parser.add_argument(
        "--date-from",
        required=True,
        help="수집 시작일 (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--date-to",
        required=True,
        help="수집 종료일 (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--platforms",
        default=None,
        help="수집 대상 플랫폼 (쉼표 구분). 미지정 시 설정된 모든 플랫폼. 예: ptt,dcard,threads",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="출력 파일 경로. 미지정 시 data/collected_{date-to}.txt",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=30,
        help="플랫폼당 최대 게시글 수 (기본: 30)",
    )
    # Platform-specific options
    parser.add_argument(
        "--ptt-boards",
        default="Gossiping,MobileComm,Lifeismoney",
        help="PTT 게시판 (쉼표 구분, 기본: Gossiping,MobileComm,Lifeismoney)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 수집 없이 설정만 확인",
    )

    return parser.parse_args()


def _validate_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        print(f"[ERROR] Invalid date format: {s} (expected YYYY-MM-DD)")
        sys.exit(1)


def main() -> None:
    args = _parse_args()
    project_root = Path(__file__).resolve().parent.parent
    env = load_env(str(project_root / ".env"))
    os.environ.update(env)  # Collectors can now access env vars

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    date_from = _validate_date(args.date_from)
    date_to = _validate_date(args.date_to)

    if date_from > date_to:
        print("[ERROR] --date-from must be <= --date-to")
        sys.exit(1)

    # Determine platforms
    all_platforms = available_platforms()
    if args.platforms:
        requested = [p.strip() for p in args.platforms.split(",")]
        platform_names = []
        for p in requested:
            matched = [ap for ap in all_platforms if ap.lower() == p.lower()]
            if not matched:
                print(f"[WARN] Unknown platform '{p}'. Available: {all_platforms}")
            else:
                platform_names.append(matched[0])
    else:
        platform_names = all_platforms

    # Output path
    output_path = args.output or f"data/collected_{args.date_to}.txt"

    print("=" * 60)
    print("SNS 데이터 수집")
    print("=" * 60)
    print(f"  키워드:     {keywords}")
    print(f"  기간:       {date_from} ~ {date_to}")
    print(f"  플랫폼:     {platform_names}")
    print(f"  게시글/플랫폼: {args.max_posts}")
    print(f"  출력 파일:  {output_path}")
    print("=" * 60)

    if args.dry_run:
        print("\n[DRY RUN] 설정 확인 완료. 실제 수집은 수행하지 않습니다.")
        return

    # Collect from each platform
    all_posts = []
    summary_rows = []
    total_posts = 0
    total_comments = 0
    total_errors = 0

    for pname in platform_names:
        print(f"\n{'─' * 40}")
        print(f"[{pname}] 수집 시작...")

        try:
            collector = get_collector(pname)
        except ValueError as e:
            print(f"[{pname}] ERROR: {e}")
            total_errors += 1
            continue

        if not collector.is_configured():
            print(f"[{pname}] SKIP: 인증 정보 미설정. .env 파일을 확인하세요.")
            summary_rows.append((pname, 0, 0, 0, "미설정"))
            continue

        extra_kwargs = {}
        if pname.lower() == "ptt":
            extra_kwargs["boards"] = [b.strip() for b in args.ptt_boards.split(",")]

        start_time = time.time()
        try:
            posts = collector.collect(
                keywords=keywords,
                date_from=str(date_from),
                date_to=str(date_to),
                max_posts=args.max_posts,
                **extra_kwargs,
            )
        except Exception as e:
            print(f"[{pname}] ERROR: {e}")
            summary_rows.append((pname, 0, 0, 1, str(e)[:40]))
            total_errors += 1
            continue

        elapsed = time.time() - start_time
        n_comments = sum(len(p.comments) for p in posts)
        all_posts.extend(posts)
        total_posts += len(posts)
        total_comments += n_comments
        summary_rows.append((pname, len(posts), n_comments, 0, ""))
        print(f"[{pname}] 완료: 게시글 {len(posts)}개, 댓글 {n_comments}개 ({elapsed:.1f}초)")

    # Write output
    if all_posts:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        raw_text = format_all(all_posts)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(raw_text)

    # Summary
    print(f"\n{'=' * 60}")
    print("수집 결과 요약")
    print(f"{'=' * 60}")
    print(f"{'플랫폼':<12} {'게시글':>6} {'댓글':>6} {'오류':>4}  비고")
    print(f"{'─' * 50}")
    for pname, n_posts, n_comments, n_errors, note in summary_rows:
        print(f"{pname:<12} {n_posts:>6} {n_comments:>6} {n_errors:>4}  {note}")
    print(f"{'─' * 50}")
    print(f"{'TOTAL':<12} {total_posts:>6} {total_comments:>6} {total_errors:>4}")
    print(f"\n출력: {output_path}" if all_posts else "\n수집된 데이터 없음")

    if all_posts:
        print(f"\n다음 단계: python3 src/run_parse_to_a.py {output_path}")


if __name__ == "__main__":
    main()
