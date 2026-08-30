"""Command line entry point.

    python -m cleared.cli run --dry-run
    python -m cleared.cli evaluate --account-id A-1002
    python -m cleared.cli verify
    python -m cleared.cli policy

Dry run is the default and the only mode that needs no credentials. `--execute`
is the single flag that can cause a real phone to ring, and `DRY_RUN=1` in the
environment refuses it outright.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .audit import AuditLog, verify_file
from .callers import Caller, FakeCaller
from .gate import evaluate
from .policy import Policy, load_policy
from .runner import BatchRun, load_accounts, run_batch
from .schema import Account, parse_iso8601
from .script import render_script
from .suppression import SuppressionList

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACCOUNTS = ROOT / "fixtures" / "accounts.json"
DEFAULT_SCENARIOS = ROOT / "fixtures" / "scenarios.json"
DEFAULT_SEED_SUPPRESSION = ROOT / "fixtures" / "suppression.jsonl"
DEFAULT_RUNTIME = ROOT / "runtime"


class CliError(RuntimeError):
    pass


def resolve_now(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return parse_iso8601(value)
    except ValueError as error:
        raise CliError(f"--now is not an ISO-8601 UTC timestamp: {error}") from error


def dry_run_requested(args: argparse.Namespace) -> bool:
    """Live calling requires --execute, and DRY_RUN=1 vetoes even that."""
    forced = os.environ.get("DRY_RUN", "").strip().lower() in {"1", "true", "yes"}
    if forced and getattr(args, "execute", False):
        raise CliError("DRY_RUN=1 is set in the environment: refusing --execute.")
    return forced or not getattr(args, "execute", False)


def open_suppression(path: Path, seed: Path | None) -> SuppressionList:
    """Open the working suppression list, seeding it on first use.

    The seed file is never written to: a run must not mutate a fixture.
    """
    if seed and seed.is_file() and not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(seed, path)
    return SuppressionList(path)


def build_caller(args: argparse.Namespace, policy: Policy, dry_run: bool) -> Caller:
    if dry_run:
        return FakeCaller.from_file(args.scenarios, policy=policy)
    from .calle_caller import CalleCaller  # imported only when a real call is wanted

    return CalleCaller(
        policy=policy,
        region=args.region,
        language=args.language,
        base_url=args.base_url,
        channel=args.channel,
        server_url=args.server_url,
        cache_root=args.cache_root,
        calle_command=args.calle_command,
        poll_interval_seconds=args.poll_interval_seconds,
        poll_timeout_seconds=args.poll_timeout_seconds,
    )


def command_run(args: argparse.Namespace) -> int:
    policy = load_policy(args.policy)
    dry_run = dry_run_requested(args)
    accounts, rejected = load_accounts(args.accounts)
    if args.account_id:
        wanted = set(args.account_id)
        accounts = [account for account in accounts if account.account_id in wanted]
        missing = wanted - {account.account_id for account in accounts}
        if missing:
            raise CliError(f"no such account in the batch: {', '.join(sorted(missing))}")
    if not accounts:
        raise CliError(f"no usable accounts in {args.accounts}")

    if args.fresh:
        for stale in (Path(args.audit), Path(args.suppression)):
            stale.unlink(missing_ok=True)

    suppression = open_suppression(Path(args.suppression), Path(args.seed_suppression))
    audit = AuditLog(args.audit)
    now = resolve_now(args.now)

    if not dry_run:
        print(
            f"LIVE MODE: up to {len(accounts)} account(s) will be gated, and every "
            f"cleared one will be dialled for real.",
            file=sys.stderr,
        )

    run = run_batch(
        accounts,
        caller=build_caller(args, policy, dry_run),
        audit=audit,
        suppression=suppression,
        policy=policy,
        now_provider=lambda: now,
        dry_run=dry_run,
        rejected_rows=rejected,
    )

    print_run(run, now)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps(run.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nResults written to {args.json}")
    return 0 if run.audit_verification and run.audit_verification.ok else 1


def print_run(run: BatchRun, now: datetime) -> None:
    summary = run.summary()
    mode = "DRY RUN (no calls placed)" if run.dry_run else "LIVE"
    print(f"Cleared to Call - {mode}")
    print(f"Policy: {summary['policy_id']} v{summary['policy_version']}")
    print(f"Clock:  {now.isoformat()}")
    print()

    header = f"{'ACCOUNT':<9} {'DECISION':<8} {'REASON / OUTCOME':<22} {'DISCLOSED':<10} AUDIT"
    print(header)
    print("-" * len(header))
    for record in run.records:
        result = record.result
        decision = "ALLOW" if record.decision.allowed else "BLOCK"
        detail = result.block_reason or result.outcome
        disclosed = "yes" if result.disclosure_given else "-"
        print(
            f"{result.account_id:<9} {decision:<8} {detail:<22} {disclosed:<10} {result.audit_ref}"
        )

    print()
    print(
        f"{summary['accounts']} account(s): {summary['blocked']} blocked, "
        f"{summary['cleared']} called, {summary['opt_outs']} opt-out(s)."
    )
    for reason, count in sorted(summary["block_reasons"].items()):
        print(f"  blocked - {reason}: {count}")
    for record in run.records:
        if record.revocation:
            print(
                f"  opt-out - {record.account.account_id} said "
                f"\"{record.revocation.text}\" -> number suppressed"
            )
        if record.error:
            print(f"  error   - {record.account.account_id}: {record.error}")
    for row in run.rejected_rows:
        print(f"  rejected input - {row}")

    audit = summary["audit"]
    if audit:
        state = "verified" if audit["ok"] else f"BROKEN at entry {audit['first_bad_index']}"
        print(f"\nAudit chain: {state} ({audit['entries_checked']} entries).")
        if not audit["ok"]:
            print(f"  {audit['reason']}")


def command_evaluate(args: argparse.Namespace) -> int:
    policy = load_policy(args.policy)
    now = resolve_now(args.now)
    suppression = open_suppression(Path(args.suppression), Path(args.seed_suppression))

    if args.account_json:
        account = Account.from_dict(json.loads(args.account_json))
    else:
        accounts, _ = load_accounts(args.accounts)
        matches = [item for item in accounts if item.account_id == args.account_id]
        if not matches:
            raise CliError(f"no such account: {args.account_id}")
        account = matches[0]

    script = render_script(account, policy)
    decision = evaluate(
        account,
        now=now,
        script_text=script.text,
        suppression=suppression,
        policy=policy,
    )
    payload = decision.to_dict()
    payload["phone_masked"] = account.masked_phone
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if decision.allowed else 2


def command_preflight(args: argparse.Namespace) -> int:
    """Gate an account, then plan the call without running it. Spends no credits.

    This is how to find out whether CALL-E can reach a region, whether the CLI is
    logged in, and whether the payload is accepted - before a phone rings.
    """
    policy = load_policy(args.policy)
    now = resolve_now(args.now)
    suppression = open_suppression(Path(args.suppression), Path(args.seed_suppression))

    accounts, _ = load_accounts(args.accounts)
    matches = [item for item in accounts if item.account_id == args.account_id]
    if not matches:
        raise CliError(f"no such account: {args.account_id}")
    account = matches[0]

    script = render_script(account, policy)
    decision = evaluate(
        account, now=now, script_text=script.text, suppression=suppression, policy=policy
    )
    print(f"Gate: {'ALLOW' if decision.allowed else 'BLOCK'}")
    for result in decision.rules:
        print(f"  {result.rule_id} {result.name}: {'pass' if result.passed else 'FAIL'} - {result.detail}")
    if not decision.allowed:
        print(f"\nBlocked: {decision.block_reason}. Nothing was planned.")
        return 2

    from .calle_caller import CalleCaller

    caller = CalleCaller(
        policy=policy,
        region=args.region,
        language=args.language,
        base_url=args.base_url,
        channel=args.channel,
        server_url=args.server_url,
        cache_root=args.cache_root,
        calle_command=args.calle_command,
    )
    print(f"\nPlanning a call to {account.masked_phone} in region {args.region} (no call is placed)...")
    plan = caller.plan_only(account, script)
    print(json.dumps(plan, indent=2))
    if plan["ready_to_run"]:
        print("\nCALL-E accepted the plan. This account can be dialled with --execute.")
        return 0
    print("\nCALL-E did not return ready_to_run. Do not spend a call on this yet.")
    return 2


def command_verify(args: argparse.Namespace) -> int:
    result = verify_file(args.audit)
    print(json.dumps({"audit_log": str(args.audit), **result.to_dict()}, indent=2))
    return 0 if result.ok else 1


def command_policy(args: argparse.Namespace) -> int:
    policy = load_policy(args.policy)
    print(f"{policy.policy_id} v{policy.policy_version} ({policy.jurisdiction})")
    print(f"Authorities: {', '.join(policy.authorities)}")
    print(f"Call window: {policy.window_label} local to the recipient")
    print(f"Timezone source: {policy.timezone_source}\n")
    for rule in policy.rules:
        print(f"{rule.id} {rule.name} [{rule.stage}] -> {rule.block_reason}")
        print(f"    {rule.requirement}")
        print(f"    authority: {rule.authority}")
        print(f"    property:  {rule.temporal_form}")
    print("\nRequired disclosure elements:")
    for element in policy.disclosure_elements:
        print(f"  {element.id}: {element.description}")
    return 0


def add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--policy", default=None, help="Path to a policy JSON file.")
    parser.add_argument("--accounts", default=str(DEFAULT_ACCOUNTS), help="Account fixture path.")
    parser.add_argument(
        "--suppression",
        default=str(DEFAULT_RUNTIME / "suppression.jsonl"),
        help="Working suppression list. Written to when someone opts out.",
    )
    parser.add_argument(
        "--seed-suppression",
        default=str(DEFAULT_SEED_SUPPRESSION),
        help="Seed list copied into the working list on first use.",
    )
    parser.add_argument("--now", default=None, help="Pin the clock, e.g. 2026-08-28T13:30:00Z.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cleared-to-call",
        description="Gate outbound collection calls against the US federal compliance policy.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Gate a batch and call the cleared accounts.")
    add_common_paths(run_parser)
    run_parser.add_argument("--audit", default=str(DEFAULT_RUNTIME / "audit.jsonl"))
    run_parser.add_argument("--scenarios", default=str(DEFAULT_SCENARIOS))
    run_parser.add_argument("--json", default=None, help="Write the full run to this JSON file.")
    run_parser.add_argument(
        "--account-id", action="append", help="Limit the run to this account. Repeatable."
    )
    run_parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete the working audit log and suppression list before running.",
    )
    mode = run_parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="Simulate the calls. This is the default."
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Place real CALL-E calls for cleared accounts. Spends credits.",
    )
    run_parser.add_argument("--region", default="US")
    run_parser.add_argument("--language", default="English")
    run_parser.add_argument("--base-url", default=None)
    run_parser.add_argument("--channel", default=None)
    run_parser.add_argument("--server-url", default=None)
    run_parser.add_argument("--cache-root", default=None)
    run_parser.add_argument("--calle-command", default=None)
    run_parser.add_argument("--poll-interval-seconds", type=float, default=10.0)
    run_parser.add_argument("--poll-timeout-seconds", type=float, default=900.0)
    run_parser.set_defaults(handler=command_run)

    evaluate_parser = subparsers.add_parser(
        "evaluate", help="Print the gate decision for one account as JSON."
    )
    add_common_paths(evaluate_parser)
    evaluate_parser.add_argument("--account-id", default=None)
    evaluate_parser.add_argument("--account-json", default=None, help="An inline account object.")
    evaluate_parser.set_defaults(handler=command_evaluate)

    preflight_parser = subparsers.add_parser(
        "preflight",
        help="Gate an account and plan the call without placing it. Spends no credits.",
    )
    add_common_paths(preflight_parser)
    preflight_parser.add_argument("--account-id", required=True)
    preflight_parser.add_argument("--region", default="US", help="Two-letter ISO country code.")
    preflight_parser.add_argument("--language", default="English")
    preflight_parser.add_argument("--base-url", default=None)
    preflight_parser.add_argument("--channel", default=None)
    preflight_parser.add_argument("--server-url", default=None)
    preflight_parser.add_argument("--cache-root", default=None)
    preflight_parser.add_argument("--calle-command", default=None)
    preflight_parser.set_defaults(handler=command_preflight)

    verify_parser = subparsers.add_parser("verify", help="Verify the audit hash chain.")
    verify_parser.add_argument("--audit", default=str(DEFAULT_RUNTIME / "audit.jsonl"))
    verify_parser.set_defaults(handler=command_verify)

    policy_parser = subparsers.add_parser("policy", help="Print the active policy.")
    policy_parser.add_argument("--policy", default=None)
    policy_parser.set_defaults(handler=command_policy)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "command", None) == "evaluate" and not (
        args.account_id or args.account_json
    ):
        print("evaluate needs --account-id or --account-json", file=sys.stderr)
        return 2
    try:
        return args.handler(args)
    except CliError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
