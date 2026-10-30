"""Argument parsing for the bare-module gateway entrypoint."""

import argparse


def parse_gateway_args():
    parser = argparse.ArgumentParser(description="Hermes Gateway - Multi-platform messaging")
    parser.add_argument("--config", "-c", help="Path to gateway config file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--idle-exit", action="store_true", help="Exit after unmanaged gateway idle timeout")
    return parser.parse_args()
