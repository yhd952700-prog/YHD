"""自升级闭环（0.3.0）。

五段：改自己 → 过门禁 → 自己出包 → 自己装（本轮只装测试目录）→ 坏了自动回去。

安全边界（与 ``release-lock`` 的完整性校验配套）：

* 执行门禁/出包时默认使用 ``release-lock/`` 的盖章副本，不信任仓库里可被改动的脚本。
* 「自装」只允许测试目录（``D:\\LiuHaoTest`` / ``D:\\LiuHaoSmoke``）；
  碰 ``D:\\LiuHao`` / ``D:\\LiuHaoData`` 直接报错，不提供绕过开关。
* 连续失败达到上限后锁死，只能 Boss 手动 ``unlock --approve``。
"""

SCHEMA = "liuhao.selfupgrade/1"

__all__ = ["SCHEMA"]
