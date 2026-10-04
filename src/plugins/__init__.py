"""Plugins 包 —— LIUHAO AI-OS 的插件实现集。

包含：
- ``base``：所有插件必须实现的 ``Plugin`` 抽象基类（即内核层别名成的
  ``PluginInterface``）；
- ``manager`` / ``registry``：插件加载与管理的基础设施；
- ``builtin``：随系统发布、可被插件加载器真实加载的内置插件。
"""
