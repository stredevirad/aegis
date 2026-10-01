"""Fault injection script for OpenTelemetry Demo via flagd feature flags.

Usage:
    python scripts/inject-fault.py --flag recommendationCacheFailure --value on
    python scripts/inject-fault.py --flag recommendationCacheFailure --recover
    python scripts/inject-fault.py --show                                       # Show current flags
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

FLAGD_CONFIG = Path(__file__).parent.parent / "external" / "opentelemetry-demo" / "src" / "flagd" / "demo.flagd.json"

# Original default values for recovery
ORIGINAL_DEFAULTS = {
    "adFailure": "off",
    "adHighCpu": "off",
    "adManualGc": "off",
    "cartFailure": "off",
    "emailMemoryLeak": "off",
    "failedReadinessProbe": "off",
    "imageSlowLoad": "off",
    "intlShippingSlowdown": "off",
    "kafkaQueueProblems": "off",
    "loadGeneratorTraffic": "on",
    "loadGeneratorVUs": "5",
    "paymentFailure": "off",
    "paymentUnreachable": "off",
    "productCatalogFailure": "off",
    "recommendationCacheFailure": "off",
}


def load_config() -> dict:
    if not FLAGD_CONFIG.exists():
        print(f"ERROR: flagd config not found at {FLAGD_CONFIG}")
        sys.exit(1)
    with open(FLAGD_CONFIG, "r") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    with open(FLAGD_CONFIG, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def show_flags() -> None:
    config = load_config()
    flags = config.get("flags", {})
    print(f"{'Flag':<35} {'Default Variant':<20} {'State'}")
    print("-" * 75)
    for name, flag in sorted(flags.items()):
        default = flag.get("defaultVariant", "?")
        state = flag.get("state", "?")
        marker = " *** ACTIVE ***" if default != ORIGINAL_DEFAULTS.get(name, "off") else ""
        print(f"{name:<35} {default:<20} {state}{marker}")


def inject_fault(flag_name: str, value: str) -> None:
    config = load_config()
    flags = config.get("flags", {})

    if flag_name not in flags:
        print(f"ERROR: Flag '{flag_name}' not found. Available flags:")
        for name in sorted(flags):
            print(f"  - {name}")
        sys.exit(1)

    flag = flags[flag_name]
    variants = flag.get("variants", {})

    if value not in variants:
        print(f"ERROR: Value '{value}' not valid for flag '{flag_name}'. Valid values:")
        for v in sorted(variants):
            print(f"  - {v}")
        sys.exit(1)

    old_default = flag["defaultVariant"]
    flag["defaultVariant"] = value
    save_config(config)

    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}] FAULT INJECTED")
    print(f"  Flag           : {flag_name}")
    print(f"  Old Default    : {old_default}")
    print(f"  New Default    : {value}")
    print(f"  Description    : {flag.get('description', 'N/A')}")
    print(f"  Config File    : {FLAGD_CONFIG}")
    print("  NOTE: flagd watches this file and will apply changes automatically.")


def recover_fault(flag_name: str) -> None:
    config = load_config()
    flags = config.get("flags", {})

    if flag_name not in flags:
        print(f"ERROR: Flag '{flag_name}' not found.")
        sys.exit(1)

    original = ORIGINAL_DEFAULTS.get(flag_name, "off")
    old_default = flags[flag_name]["defaultVariant"]
    flags[flag_name]["defaultVariant"] = original
    save_config(config)

    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}] FAULT RECOVERED")
    print(f"  Flag           : {flag_name}")
    print(f"  Restored to    : {original}")
    print(f"  Was            : {old_default}")


def recover_all() -> None:
    config = load_config()
    flags = config.get("flags", {})
    restored = []

    for name, original in ORIGINAL_DEFAULTS.items():
        if name in flags and flags[name]["defaultVariant"] != original:
            flags[name]["defaultVariant"] = original
            restored.append(name)

    save_config(config)
    ts = datetime.now(timezone.utc).isoformat()
    if restored:
        print(f"[{ts}] ALL FAULTS RECOVERED: {', '.join(restored)}")
    else:
        print(f"[{ts}] No active faults to recover.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject/recover faults via flagd feature flags")
    parser.add_argument("--flag", type=str, help="Flag name to modify")
    parser.add_argument("--value", type=str, help="Variant value to set (e.g. 'on', '50%%')")
    parser.add_argument("--recover", action="store_true", help="Recover the specified flag to default")
    parser.add_argument("--recover-all", action="store_true", help="Recover all flags to defaults")
    parser.add_argument("--show", action="store_true", help="Show current flag states")

    args = parser.parse_args()

    if args.show:
        show_flags()
    elif args.recover_all:
        recover_all()
    elif args.flag and args.recover:
        recover_fault(args.flag)
    elif args.flag and args.value:
        inject_fault(args.flag, args.value)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
