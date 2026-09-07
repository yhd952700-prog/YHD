#!/usr/bin/env python
"""
Rollback Script for LiuHao AI OS

Provides safe rollback capabilities for deployments and configurations.
"""

import os
import sys
import json
import shutil
import argparse
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logger = logging.getLogger(__name__)


class RollbackManager:
    """Manages rollback operations for deployments and configurations."""
    
    def __init__(self, config_dir: Path = Path("config")):
        self.config_dir = config_dir
        self.history_dir = config_dir / "rollback_history"
        self.history_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    def create_checkpoint(self, name: str, description: str = "") -> Path:
        """
        Create a configuration checkpoint.
        
        Args:
            name: Checkpoint name
            description: Optional description
            
        Returns:
            Path to checkpoint file
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        checkpoint_name = f"{name}_{timestamp}"
        checkpoint_path = self.history_dir / f"{checkpoint_name}.json"
        
        # Collect current configuration
        config_data = self._collect_config()
        
        checkpoint = {
            "name": checkpoint_name,
            "description": description,
            "created_at": datetime.now().isoformat(),
            "config": config_data,
        }
        
        with open(checkpoint_path, 'w') as f:
            json.dump(checkpoint, f, indent=2)
        
        logger.info(f"Checkpoint created: {checkpoint_path}")
        return checkpoint_path
    
    def _collect_config(self) -> Dict[str, Any]:
        """Collect current configuration state."""
        config_data = {}
        
        # Collect config files
        for config_file in self.config_dir.rglob("*.py"):
            if config_file.is_file():
                try:
                    rel_path = config_file.relative_to(self.config_dir)
                    config_data[str(rel_path)] = config_file.read_text()
                except Exception as e:
                    logger.warning(f"Failed to read {config_file}: {e}")
        
        # Collect deployment info if exists
        deployment_file = Path("deployment_info.json")
        if deployment_file.exists():
            try:
                with open(deployment_file, 'r') as f:
                    config_data["deployment"] = json.load(f)
            except Exception:
                pass
        
        return config_data
    
    def list_checkpoints(self) -> List[Dict[str, Any]]:
        """List all available checkpoints."""
        checkpoints = []
        for cp_file in self.history_dir.glob("*.json"):
            try:
                with open(cp_file, 'r') as f:
                    data = json.load(f)
                checkpoints.append({
                    "file": cp_file.name,
                    "name": data.get("name", cp_file.stem),
                    "description": data.get("description", ""),
                    "created_at": data.get("created_at", ""),
                })
            except Exception as e:
                logger.warning(f"Failed to read checkpoint {cp_file}: {e}")
        
        return sorted(checkpoints, key=lambda x: x["created_at"], reverse=True)
    
    def rollback_to_checkpoint(self, checkpoint_name: str, dry_run: bool = False) -> bool:
        """
        Rollback to a specific checkpoint.
        
        Args:
            checkpoint_name: Checkpoint file name (without .json)
            dry_run: If True, only show what would be changed
            
        Returns:
            True if successful
        """
        checkpoint_file = self.history_dir / f"{checkpoint_name}.json"
        if not checkpoint_file.exists():
            # Try with .json suffix
            checkpoint_file = self.history_dir / f"{checkpoint_name}"
            if not checkpoint_file.exists():
                logger.error(f"Checkpoint not found: {checkpoint_name}")
                return False
        
        try:
            with open(checkpoint_file, 'r') as f:
                checkpoint = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read checkpoint: {e}")
            return False
        
        config_data = checkpoint.get("config", {})
        
        if dry_run:
            logger.info("=== DRY RUN - Changes that would be made ===")
            for path in config_data:
                logger.info(f"  Would restore: {path}")
            return True
        
        # Backup current state first
        backup_name = f"pre_rollback_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        backup_path = self.history_dir / f"{backup_name}.json"
        current_config = self._collect_config()
        
        backup_data = {
            "name": backup_name,
            "description": "Pre-rollback backup",
            "created_at": datetime.now().isoformat(),
            "config": current_config,
        }
        
        with open(backup_path, 'w') as f:
            json.dump(backup_data, f, indent=2)
        
        logger.info(f"Pre-rollback backup created: {backup_path}")
        
        # Restore configuration
        for rel_path, content in config_data.items():
            target_file = self.config_dir / rel_path
            target_file.parent.mkdir(parents=True, exist_ok=True)
            
            if dry_run:
                logger.info(f"Would write: {target_file}")
            else:
                target_file.write_text(content)
                logger.info(f"Restored: {target_file}")
        
        logger.info(f"Rollback to {checkpoint_name} completed successfully")
        return True
    
    def rollback_deployment(self, target_version: str, dry_run: bool = False) -> bool:
        """
        Rollback to a specific deployment version.
        
        Args:
            target_version: Version tag to rollback to
            dry_run: If True, only show what would be done
            
        Returns:
            True if successful
        """
        # Check if deployment info exists
        deployment_file = Path("deployment_info.json")
        if not deployment_file.exists():
            logger.error("No deployment info found")
            return False
        
        with open(deployment_file, 'r') as f:
            deployment = json.load(f)
        
        # Check if target version exists in history
        versions = deployment.get("versions", {})
        if target_version not in versions:
            logger.error(f"Version not found: {target_version}")
            logger.info(f"Available versions: {list(versions.keys())}")
            return False
        
        target_info = versions[target_version]
        
        if dry_run:
            logger.info(f"=== DRY RUN - Would rollback to version {target_version} ===")
            logger.info(f"  Image: {target_info.get('image')}")
            logger.info(f"  Deployed at: {target_info.get('deployed_at')}")
            logger.info(f"  Config: {target_info.get('config')}")
            return True
        
        # In a real implementation, this would trigger the actual deployment rollback
        # For now, we record the rollback intention
        rollback_record = {
            "rolled_back_to": target_version,
            "rolled_back_at": datetime.now().isoformat(),
            "previous_version": deployment.get("current_version"),
        }
        
        deployment["current_version"] = target_version
        deployment["rollback_history"] = deployment.get("rollback_history", [])
        deployment["rollback_history"].append(rollback_record)
        
        with open(deployment_file, 'w') as f:
            json.dump(deployment, f, indent=2)
        
        logger.info(f"Deployment rolled back to version: {target_version}")
        return True


def main():
    parser = argparse.ArgumentParser(description="Rollback management for LiuHao AI OS")
    parser.add_argument("action", choices=["checkpoint", "list", "rollback", "rollback-deployment"])
    parser.add_argument("--name", help="Checkpoint name")
    parser.add_argument("--description", default="", help="Checkpoint description")
    parser.add_argument("--checkpoint", help="Checkpoint to rollback to")
    parser.add_argument("--version", help="Deployment version to rollback to")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    manager = RollbackManager()
    
    if args.action == "checkpoint":
        if not args.name:
            print("Error: --name required for checkpoint")
            sys.exit(1)
        path = manager.create_checkpoint(args.name, args.description)
        print(f"Checkpoint created: {path}")
    
    elif args.action == "list":
        checkpoints = manager.list_checkpoints()
        for cp in checkpoints:
            print(f"{cp['name']} - {cp['description']} - {cp['created_at']}")
    
    elif args.action == "rollback":
        if not args.checkpoint:
            print("Error: --checkpoint required for rollback")
            sys.exit(1)
        success = manager.rollback_to_checkpoint(args.checkpoint, args.dry_run)
        sys.exit(0 if success else 1)
    
    elif args.action == "rollback-deployment":
        if not args.version:
            print("Error: --version required for deployment rollback")
            sys.exit(1)
        success = manager.rollback_deployment(args.version, args.dry_run)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()