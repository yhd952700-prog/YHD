"""Verify all module imports resolve correctly for LiuHao AI OS."""

import sys
import os

# Ensure src is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

def verify_module(module_path: str) -> bool:
    """Verify a module can be imported without errors."""
    try:
        # Convert to Python import path
        rel_path = os.path.relpath(module_path, 'src')
        import_path = rel_path.replace(os.sep, '.').rstrip('.py')
        
        __import__(import_path)
        print(f"✓ {module_path} - imports OK")
        return True
    except ImportError as e:
        print(f"✗ {module_path} - ImportError: {e}")
        return False
    except SyntaxError as e:
        print(f"✗ {module_path} - SyntaxError: {e}")
        return False
    except Exception as e:
        print(f"✗ {module_path} - Error: {e}")
        return False


def main():
    """Run verification on all src modules."""
    src_dir = os.path.join(os.path.dirname(__file__), '..', 'src')
    
    # Walk all Python files
    for root, dirs, files in os.walk(src_dir):
        # Skip __pycache__
        if '__pycache__' in root:
            continue
            
        for f in files:
            if f.endswith('.py'):
                filepath = os.path.join(root, f)
                verify_module(filepath)
    
    # Check test files too
    test_dir = os.path.join(os.path.dirname(__file__), '..', 'tests')
    if os.path.exists(test_dir):
        print("\n--- Testing files ---")
        for root, dirs, files in os.walk(test_dir):
            if '__pycache__' in root:
                continue
            for f in files:
                if f.endswith('.py'):
                    filepath = os.path.join(root, f)
                    verify_module(filepath)
    
    print("\nVerification complete.")


if __name__ == "__main__":
    main()