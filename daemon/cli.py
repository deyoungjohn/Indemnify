#!/usr/bin/env python3
import os
import sys
import argparse

def main():
    parser = argparse.ArgumentParser(description="Indemnify Protocol Agent CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # "explain" command
    explain_parser = subparsers.add_parser("explain", help="Print the Indemnify Agent Manual")
    explain_parser = subparsers.add_parser("docs", help="Print the Indemnify Agent Manual")
    
    args = parser.parse_args()
    
    if args.command in ["explain", "docs"]:
        # Find the AGENT_MANUAL.md file relative to this script
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        manual_path = os.path.join(base_dir, "docs", "AGENT_MANUAL.md")
        
        if os.path.exists(manual_path):
            with open(manual_path, "r", encoding="utf-8") as f:
                print(f.read())
        else:
            print(f"Error: Could not find AGENT_MANUAL.md at {manual_path}", file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    main()
