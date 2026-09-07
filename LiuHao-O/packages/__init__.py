"""liuhao_x — LIUHAO X 十源 packages 层包根.

连字符导入屏障的解法：pyproject.toml 以 ``package-dir`` 把本目录
（``LiuHao-O/packages``）映射为可分发的 ``liuhao_x`` 包，使
``import liuhao_x.agent`` 等价于 ``import packages.agent``。
"""

__title__ = "liuhao-x"
__version__ = "3.0.0"
