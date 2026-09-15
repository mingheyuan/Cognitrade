"""Use the pinned SDK runtime's normal ChatGPT device login; never read tokens."""
import argparse
import os

from codex_cli_bin import bundled_codex_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="Check login without modifying it")
    args = parser.parse_args()
    binary = str(bundled_codex_path())
    os.execv(binary, [binary, "--config", 'forced_login_method="chatgpt"',
                     "login", "status" if args.status else "--device-auth"])


if __name__ == "__main__":
    main()
