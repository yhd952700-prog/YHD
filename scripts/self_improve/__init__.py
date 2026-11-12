"""只读缺口扫描器（self-improve 地基）。

本目录不是产品代码。它读取仓库里的登记/报告/门禁输出，产出一份
`outputs/self-improve/gap-report.{json,md}`，回答「哪里有问题、多严重、先修哪个」。

边界：
* 不修改业务代码、不修改门禁脚本；
* 唯一写盘位置是 `outputs/self-improve/`（pytest 的 JUnit 与红证临时文件也在其中）；
* 判定规则全部写在本目录的 Python 里，不依赖模型判断。
"""
