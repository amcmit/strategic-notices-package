"""Small, explicit commands for public design generation and owner sealing."""

from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
from stat import S_IMODE

from .suite import seal_confirmation, verify_confirmation_metadata, write_design


def main() -> None:
    parser = argparse.ArgumentParser(prog="sn-study")
    commands = parser.add_subparsers(dest="command", required=True)

    design = commands.add_parser("build-design")
    design.add_argument("--output", type=Path, required=True)
    design.add_argument("--seed", type=int, required=True)

    seal = commands.add_parser("owner-seal")
    seal.add_argument("--repository-root", type=Path, required=True)
    seal.add_argument("--seed-file", type=Path, required=True)
    seal.add_argument("--private-root", type=Path, required=True)
    seal.add_argument("--public-metadata", type=Path, required=True)

    verify = commands.add_parser("verify-confirmation-metadata")
    verify.add_argument("--path", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "build-design":
        manifest = write_design(args.output, master_seed=args.seed)
        digest = sha256((args.output / "manifest.json").read_bytes()).hexdigest()
    elif args.command == "owner-seal":
        repository_root = args.repository_root.resolve()
        seed_file = args.seed_file.resolve()
        if seed_file.is_relative_to(repository_root):
            parser.error("the private seed file must be outside the repository")
        if S_IMODE(seed_file.stat().st_mode) & 0o077:
            parser.error("the private seed file must not be group/world readable")
        master_seed = int(seed_file.read_text().strip())
        if master_seed < 0 or master_seed.bit_length() < 128:
            parser.error("the private seed must be a nonnegative integer of at least 128 bits")
        manifest = seal_confirmation(
            args.private_root, args.public_metadata,
            private_master_seed=master_seed, repository_root=repository_root,
        )
        digest = sha256(args.public_metadata.read_bytes()).hexdigest()
    else:
        digest = verify_confirmation_metadata(args.path)
        print(f"verify-confirmation-metadata: 16 public rows valid; SHA-256 {digest}")
        return
    print(f"{args.command}: {manifest['count']} cases; public manifest SHA-256 {digest}")


if __name__ == "__main__":
    main()
