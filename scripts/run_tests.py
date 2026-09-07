#!/usr/bin/env python
"""
Test Runner for LiuHao AI OS

Provides command-line interface for running tests with various options.
"""

import sys
import argparse
import subprocess
from pathlib import Path


def run_tests(
    test_type: str = "all",
    coverage: bool = False,
    coverage_threshold: int = 80,
    verbose: bool = True,
    slow: bool = False,
    parallel: bool = False,
    pattern: str = None,
) -> int:
    """
    Run tests with specified options.
    
    Args:
        test_type: "all", "unit", "integration", "e2e", "security", "performance"
        coverage: Enable coverage reporting
        coverage_threshold: Minimum coverage percentage
        verbose: Verbose output
        slow: Run slow tests
        parallel: Run tests in parallel
        pattern: Test pattern to match
    
    Returns:
        Exit code (0 = success)
    """
    cmd = ["python", "-m", "pytest"]
    
    # Verbosity
    if verbose:
        cmd.append("-v")
    else:
        cmd.append("-q")
    
    # Test type selection
    if test_type == "unit":
        cmd.extend(["-m", "unit"])
    elif test_type == "integration":
        cmd.extend(["-m", "integration"])
    elif test_type == "e2e":
        cmd.extend(["-m", "e2e"])
    elif test_type == "security":
        cmd.extend(["-m", "security"])
    elif test_type == "performance":
        cmd.extend(["-m", "performance"])
    elif test_type == "deployment":
        cmd.extend(["-m", "deployment"])
    # "all" runs everything
    
    # Slow tests
    if slow:
        cmd.append("--run-slow")
    
    # Coverage
    if coverage:
        cmd.extend([
            f"--cov=src",
            f"--cov-report=term-missing",
            f"--cov-report=html:tests/output/coverage_html",
            f"--cov-report=xml:tests/output/coverage.xml",
            f"--cov-fail-under=80",
        ])
    
    # Parallel execution
    if parallel:
        cmd.extend(["-n", "auto"])
    
    # Pattern matching
    if pattern:
        cmd.extend(["-k", pattern])
    
    # Output formatting
    cmd.extend(["--tb=short"])
    
    print(f"Running command: {' '.join(cmd)}")
    return subprocess.run(cmd).returncode


def main():
    parser = argparse.ArgumentParser(
        description="Test runner for LiuHao AI OS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/run_tests.py                    # Run all tests
  python scripts/run_tests.py --unit             # Run unit tests only
  python scripts/run_tests.py --integration      # Run integration tests
  python scripts/run_tests.py --e2e              # Run E2E tests
  python scripts/run_tests.py --coverage         # Run with coverage
  python scripts/run_tests.py --slow             # Include slow tests
  python scripts/run_tests.py --parallel         # Run in parallel
  python scripts/run_tests.py -k "test_span"     # Run tests matching pattern
        """,
    )
    
    parser.add_argument(
        "test_type",
        nargs="?",
        default="all",
        choices=["all", "unit", "integration", "e2e", "security", "performance", "deployment"],
        help="Type of tests to run",
    )
    
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Enable coverage reporting",
    )
    
    parser.add_argument(
        "--coverage-threshold",
        type=int,
        default=80,
        help="Minimum coverage percentage (default: 80)",
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=True,
        help="Verbose output",
    )
    
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Quiet output",
    )
    
    parser.add_argument(
        "--slow",
        action="store_true",
        help="Run slow tests",
    )
    
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run tests in parallel",
    )
    
    parser.add_argument(
        "-k", "--pattern",
        type=str,
        help="Run tests matching pattern",
    )
    
    args = parser.parse_args()
    
    # Handle verbosity
    verbose = not args.quiet
    
    exit_code = run_tests(
        test_type=args.test_type,
        coverage=args.coverage,
        coverage_threshold=args.coverage_threshold,
        verbose=verbose,
        slow=args.slow,
        parallel=args.parallel,
        pattern=args.pattern,
    )
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()