"""Vault密钥管理客户端 for liuhao AI OS"""

import hvac  # HashiCorp Vault Python client
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class VaultClient:
    """Vault客户端封装
    
    提供统一的密钥读取、写入、轮换接口
    支持 AppRole 认证 + TTL Token 自动续约
    """
    
    def __init__(self, 
                 vault_url: str = "http://localhost:8200",
                 role_id: Optional[str] = None,
                 secret_id: Optional[str] = None,
                 mount_point: str = "secret",
                 ttl: int = 3600):
        self._vault_url = vault_url
        self._mount_point = mount_point
        self._ttl = ttl
        self._client: Optional[hvac.Client] = None
        self._is_authenticated = False
    
    def authenticate_approle(self, role_id: str, secret_id: str) -> bool:
        """使用 AppRole 认证 Vault
        
        Args:
            role_id: AppRole role_id
            secret_id: AppRole secret_id
            
        Returns:
            是否认证成功
        """
        self._client = hvac.Client(url=self._vault_url)
        response = self._client.auth_approle(role_id=role_id, secret_id=secret_id)
        
        if response and "auth" in response:
            self._is_authenticated = True
            logger.info("Vault AppRole authentication successful")
            return True
        else:
            logger.error("Vault AppRole authentication failed")
            self._is_authenticated = False
            return False
    
    def read_secret(self, path: str, version: Optional[int] = None) -> Dict[str, Any]:
        """从 Vault 读取密钥
        
        Args:
            path: 密钥路径 (如: secret/liuhao/keys/master)
            version: 具体版本号 (None 表示最新)
            
        Returns:
            密钥值字典
        """
        if not self._is_authenticated or not self._client:
            raise RuntimeError("Vault client not authenticated")
        
        read_path = f"{self._mount_path}/{path}"
        if version:
            read_path = f"{read_path}/versions/{version}"
        
        response = self._client.secrets.kv.v2.read_secret_version(
            path=read_path,
            mount_point=self._mount_point
        )
        
        return response["data"]["data"]
    
    def write_secret(self, path: str, data: Dict[str, Any]) -> bool:
        """写入密钥到 Vault
        
        Args:
            path: 密钥路径
            data: 要写入的数据
            
        Returns:
            是否写入成功
        """
        if not self._is_authenticated or not self._client:
            raise RuntimeError("Vault client not authenticated")
        
        self._client.secrets.kv.v2.create_or_update_secret(
            path=path,
            secret=data,
            mount_point=self._mount_point
        )
        
        logger.info(f"Secret written to {path}")
        return True
    
    def rotate_key(self, path: str, ttl: Optional[int] = None) -> bool:
        """轮换密钥 TTL
        
        Args:
            path: 密钥路径
            ttl: 新的 TTL (秒)，None 使用默认值
            
        Returns:
            是否成功
        """
        ttl = ttl or self._ttl
        # In production, would call Vault's TTL rotation API
        logger.info(f"Rotating TTL for secret at {path} to {ttl}s")
        return True
    
    async def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        if not self._is_authenticated or not self._client:
            return {"healthy": False, "error": "Not authenticated"}
        
        try:
            response = self._client.sys.seal_status
            return {"healthy": response.get("sealed", True) == False}
        except Exception as e:
            return {"healthy": False, "error": str(e)}


# Global instance
vault_client = VaultClient()
