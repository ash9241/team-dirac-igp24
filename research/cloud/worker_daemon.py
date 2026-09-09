#!/usr/bin/env python3
"""Compatibility entry point for the credential-free generation worker."""

try:
    from cloud.generator_worker import main
except ModuleNotFoundError:  # direct execution from the cloud directory
    from generator_worker import main


if __name__ == "__main__":
    main()
