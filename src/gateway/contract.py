"""前后端 API 契约版本（单一事实来源）。

控制台构建产物里会带一份签章（``console/console-contract.json``），其
``api_contract`` 必须等于这里的值；升级器在落盘前与自检时都会比对，
不一致就拒绝升级/自动回滚。改接口形状时必须同时提升该版本并重建控制台。
"""

API_CONTRACT_VERSION = "1"
