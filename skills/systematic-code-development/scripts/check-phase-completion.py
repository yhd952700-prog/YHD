"""Check which phases have 100% test pass for LiuHao AI OS."""

import subprocess
import sys
import os

def run_phase_test(phase_name: str, ignore_dirs: list = None) -> dict:
    """Run pytest on a phase directory and return pass/fail stats."""
    base = os.path.join(os.path.dirname(__file__), '..', 'tests')
    
    # Build pytest command
    cmd = [sys.executable, '-m', 'pytest']
    
    # Add ignore patterns if provided
    if ignore_dirs:
        for ign in ignore_dirs:
            cmd.extend(['--ignore', ign])
    
    # Run tests
    try:
        result = subprocess.run(
            cmd,
            cwd=base,
            capture_output=True,
            text=True,
            timeout=120
        )
        
        # Parse output for passes/failures
        output = result.stdout + result.stderr
        
        # Simple counting - look for pass/fail indicators
        passed = 0
        failed = 0
        
        for line in output.split('\n'):
            if 'PASSED' in line or 'passed' in line.lower():
                # Extract number
                import re
                nums = re.findall(r'(\d+))', line)
                if nums:
                    passed += int(nums[0])
            if 'FAILED' in line or 'failed' in line.lower():
                nums = re.findall(r'(\d+))', line)
                if nums:
                    failed += int(nums[0])
        
        total = passed + failed
        percent = (passed / total * 100) if total > 0 else 0
        
        return {
            'phase': phase_name,
            'total': total,
            'passed': passed,
            'failed': failed,
            'percent': percent,
            'healthy': percent == 100.0
        }
    except subprocess.TimeoutExpired:
        return {'phase': phase_name, 'error': 'timeout', 'healthy': False}
    except Exception as e:
        return {'phase': phase_name, 'error': str(e), 'healthy': False}


def main():
    """Check all phase test completion status."""
    base = os.path.join(os.path.dirname(__file__), '..', 'tests')
    
    # Define phase directories and their ignore patterns
    phases = [
        ('Phase 1-5 Core', base, []),
        ('Phase 6 SRE', os.path.join(base, 'sre'), ['tests/load']),
        ('Phase 7 UI', os.path.join(base, 'frontend'), []),
    ]
    
    print("=" * 60)
    print("LiuHao AI OS Phase Test Completion Report")
    print("=" * 60)
    
    all_healthy = True
    for phase_name, phase_dir, ignore_dirs in phases:
        if not os.path.exists(phase_dir):
            print(f"\n⚠ {phase_name}: directory not found")
            continue
            
        result = run_phase_test(phase_name, ignore_dirs)
        
        if 'error' in result:
            print(f"\n❌ {phase_name}: {result['error']}")
            all_healthy = False
        else:
            status_color = "✅" if result['healthy'] else "⚠"
            print(f"\n{status_color} {phase_name}: {result['passed']}/{result['total']} ({result['percent']:.1f}%)")
            
            if not result['healthy']:
                all_healthy = False
    
    print("\n" + "=" * 60)
    if all_healthy:
        print("🎉 ALL PHASES AT 100% - LiuHao AI OS Y1 Complete!")
    else:
        print("⚠ SOME PHASES NEED ATTENTION")
    print("=" * 60)


if __name__ == "__main__":
    main()