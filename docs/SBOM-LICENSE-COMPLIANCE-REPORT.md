# SBOM 与许可证合规报告 —— 鎏灏 LiuHao AI OS 0.1.1

> 审计日期：2026-11-08T07:16:07+08:00
> 审计脚本：[sbom_audit.py](file:///D:/LiuHao-AI-OS/dist/release-gate/sbom_audit.py)
> SBOM：[sbom-cyclonedx.json](file:///D:/LiuHao-AI-OS/dist/release-gate/sbom-cyclonedx.json)
> 机器证据：[license-audit.json](file:///D:/LiuHao-AI-OS/dist/release-gate/license-audit.json)
> 逐包清单：[license-inventory.csv](file:///D:/LiuHao-AI-OS/dist/release-gate/license-inventory.csv)

## 1. 结论

**🟢 MATERIALS COMPLETE —— 材料层面已齐备，可以对外分发。未检出强限制性许可证；2 个 LGPL-2.1 组件已附全文、源码与可重链接方案。唯一剩余的不是材料，而是法务判断：书面确认 LGPL 静态链接方案满足第 6 条（见 1.1 与第 8 节）。**

- 强限制性许可证（GPL/AGPL/SSPL/CC-BY-NC/Elastic/BSL/OpenRAIL）：**0 个**。
- 弱 copyleft（LGPL/MPL/EPL/CDDL）：**4 个** —— certifi（MPL-2.0）、orjson（MPL-2.0 AND (Apache-2.0 OR MIT)）、adodbapi（pywin32 311 内置）（LGPL-2.1）、iconv（lxml 6.1.3 二进制 wheel 内置，静态链接）（LGPL-2.1）。
- 未知/未确认许可证：**0 个** —— 无。
- Python 发行包：**70 个**；内嵌/运行时组件：**22 个**；前端 bundle 依赖：**3 个**。
- 许可证文本缺失/不完整项：**0 个**。

**判定口径**：本报告不把“清单里写了”当作合规证据，而是以安装树实际文件、PE VersionInfo、Python 运行时探针、许可证全文内容为准。就本次交付物而言，**不存在会阻断商用分发的强 copyleft 许可证**（判定依据：`packaging/licenses/` 16 份许可证全文 + `docs/compliance/sbom-cyclonedx.json` 的 `components` 共 95 项）；两个 LGPL-2.1 嵌入组件已按第 3.1 节附全文、源码与可重链接方案，7 项许可证文本缺口已补齐。**材料层面已齐备**；剩余唯一事项是法务书面确认 LGPL 静态链接方案，属于**法律判断而非材料缺失**。

### 1.1 限制性许可证与商用分发的冲突判定

| 许可证 | 组件 | 与商用分发是否冲突 | 说明 |
|---|---|---|---|
| GPL-2.0/3.0、AGPL-3.0、SSPL、CC-BY-NC、Elastic-2.0、BSL/BUSL、OpenRAIL、Commons-Clause | 无 | 不适用 | 本次 payload **0 个**；一旦出现即阻断商用分发，必须移除或取得单独商业授权。 |
| MPL-2.0 | certifi 2026.7.22、orjson 3.12.0 | **不冲突** | 文件级弱 copyleft。以未修改的 wheel 原样分发时，只需保留许可证文本；若修改 MPL 覆盖文件，需公开被修改文件的源码。两份全文均已随包。 |
| LGPL-2.1 | adodbapi 2.6.2.0（pywin32 311 内置） | **不冲突，义务已闭环** | 纯 Python、19 个 `.py` 源码随包，不存在静态链接；LGPL 全文与第 3.1.1 节说明已随包。 |
| LGPL-2.1 | iconv（lxml 6.1.3 内置，静态链接） | **不冲突，材料已闭环；待法务确认** | 已附 LGPL-2.1 全文、libiconv 1.17 源码要约（含 SHA256）与重链接/动态替换步骤；第 6 条是否满足属法务判断。 |
| Tix License（BSD 风格） | Tix 8.4.3 | **不冲突** | 全文已随包 `licenses/Tix-License.txt`；产品运行时不加载 Tix。 |

## 2. 审计对象与证据链

| 项 | 值 |
|---|---|
| 安装器 | `未指定（本轮只审计 payload；dist/installer 无正式成品）` |
| 安装器 SHA256 | `未计算` |
| 实装树 | `D:\LiuHao-AI-OS\dist\payload\app`（本轮审计对象为 payload 本体；dist/installer 无正式成品） |
| Python 发行包数量 | 70（`runtime/Lib/site-packages/*.dist-info`） |
| Python 版本 | 3.12.14 |
| THIRD-PARTY-NOTICES | `D:\LiuHao-AI-OS\dist\payload\app\THIRD-PARTY-NOTICES.txt`（26139 字节，SHA256 `32dbe72a7a092be44cc4eb7bfddf7c80372f76f5e70db84ac8a9d46c9a95acc6`） |
| 生成器核对 | 逐字节一致 |

> 说明：仓库里没有 `packaging/third-party-notices` 目录或静态清单文件；实际可核对对象是
> 安装树根目录 `THIRD-PARTY-NOTICES.txt` 与生成器 `packaging/third_party_notices.py`。
> `dist/final-*`、`dist/staging-*` 下保留的历史 payload 是**旧构建产物**（例如 final-72fd 为 72 个
> dist-info 且没有 notices/licenses），不是本次交付安装器展开后的内容，故不作为审计基准。

## 3. Python 发行包（70 个）

| # | 名称 | 版本 | 声明许可证 | 归类 | 许可证文本 | 状态 |
|---|---|---|---|---|---|---|
| 1 | aiohappyeyeballs | 2.7.1 | PSF-2.0 | permissive | `aiohappyeyeballs-2.7.1.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 2 | aiohttp | 3.14.4 | Apache-2.0 AND MIT | permissive | `aiohttp-3.14.4.dist-info/licenses/LICENSE.txt`<br>`aiohttp-3.14.4.dist-info/licenses/vendor/llhttp/LICENSE` | 🟢 文本随包 |
| 3 | aiosignal | 1.4.0 | Apache-2.0 | permissive | `aiosignal-1.4.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 4 | aiosqlite | 0.22.1 | MIT | permissive | `aiosqlite-0.22.1.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 5 | annotated-doc | 0.0.5 | MIT | permissive | `annotated_doc-0.0.5.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 6 | annotated-types | 0.8.0 | MIT | permissive | `annotated_types-0.8.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 7 | anthropic | 1.3.0 | MIT | permissive | `anthropic-1.3.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 8 | anyio | 4.15.1 | MIT | permissive | `anyio-4.15.1.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 9 | attrs | 26.1.0 | MIT | permissive | `attrs-26.1.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 10 | bcrypt | 5.0.0 | Apache-2.0 | permissive | `bcrypt-5.0.0.dist-info/LICENSE` | 🟢 文本随包 |
| 11 | certifi | 2026.7.22 | MPL-2.0 | weak-copyleft | `certifi-2026.7.22.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 12 | cffi | 2.1.1 | MIT-0 | permissive | `cffi-2.1.1.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 13 | charset-normalizer | 3.5.1 | MIT | permissive | `charset_normalizer-3.5.1.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 14 | click | 8.5.0 | BSD-3-Clause | permissive | `click-8.5.0.dist-info/licenses/LICENSE.txt` | 🟢 文本随包 |
| 15 | cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause | permissive | `cryptography-50.0.1.dist-info/licenses/LICENSE`<br>`cryptography-50.0.1.dist-info/licenses/LICENSE.APACHE` 等 3 个 | 🟢 文本随包 |
| 16 | docstring_parser | 0.18.0 | MIT | permissive | `docstring_parser-0.18.0.dist-info/licenses/LICENSE.md` | 🟢 文本随包 |
| 17 | email-validator | 2.3.0 | Unlicense | permissive | `email_validator-2.3.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 18 | fastapi | 0.141.1 | MIT | permissive | `fastapi-0.141.1.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 19 | frozenlist | 1.8.0 | Apache-2.0 | permissive | `frozenlist-1.8.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 20 | google-auth | 2.57.1 | Apache-2.0 | permissive | `google_auth-2.57.1.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 21 | google-genai | 2.22.0 | Apache-2.0 | permissive | `google_genai-2.22.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 22 | googleapis-common-protos | 1.75.3 | Apache-2.0 | permissive | `googleapis_common_protos-1.75.3.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 23 | greenlet | 3.5.5 | MIT AND PSF-2.0 | permissive | `greenlet-3.5.5.dist-info/licenses/LICENSE`<br>`greenlet-3.5.5.dist-info/licenses/LICENSE.PSF` | 🟢 文本随包 |
| 24 | h11 | 0.16.0 | MIT | permissive | `h11-0.16.0.dist-info/licenses/LICENSE.txt` | 🟢 文本随包 |
| 25 | h2 | 4.4.1 | MIT | permissive | `h2-4.4.1.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 26 | hpack | 4.2.0 | MIT | permissive | `hpack-4.2.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 27 | httpcore | 1.0.9 | BSD-3-Clause | permissive | `httpcore-1.0.9.dist-info/licenses/LICENSE.md` | 🟢 文本随包 |
| 28 | httpcore2 | 2.12.0 | BSD-3-Clause | permissive | `httpcore2-2.12.0.dist-info/licenses/LICENSE.md` | 🟢 文本随包 |
| 29 | httpx | 0.28.1 | BSD-3-Clause | permissive | `httpx-0.28.1.dist-info/licenses/LICENSE.md` | 🟢 文本随包 |
| 30 | httpx2 | 2.12.0 | BSD-3-Clause | permissive | `httpx2-2.12.0.dist-info/licenses/LICENSE.md` | 🟢 文本随包 |
| 31 | hvac | 2.4.0 | Apache-2.0 | permissive | `hvac-2.4.0.dist-info/LICENSE.txt` | 🟢 文本随包 |
| 32 | hyperframe | 6.1.0 | MIT | permissive | `hyperframe-6.1.0.dist-info/LICENSE` | 🟢 文本随包 |
| 33 | idna | 3.19 | BSD-3-Clause | permissive | `idna-3.19.dist-info/licenses/LICENSE.md` | 🟢 文本随包 |
| 34 | jiter | 0.16.0 | MIT | permissive | `jiter-0.16.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 35 | lxml | 6.1.3 | BSD-3-Clause | permissive | `lxml-6.1.3.dist-info/licenses/LICENSE.txt`<br>`lxml-6.1.3.dist-info/licenses/LICENSES.txt` | 🟢 文本随包 |
| 36 | multidict | 6.9.1 | Apache-2.0 | permissive | `multidict-6.9.1.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 37 | openai | 3.13.0 | Apache-2.0 | permissive | `openai-3.13.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 38 | orjson | 3.12.0 | MPL-2.0 AND (Apache-2.0 OR MIT) | weak-copyleft | `orjson-3.12.0.dist-info/licenses/LICENSE-APACHE`<br>`orjson-3.12.0.dist-info/licenses/LICENSE-MIT` 等 3 个 | 🟢 文本随包 |
| 39 | packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | permissive | `packaging-26.3.dist-info/licenses/LICENSE`<br>`packaging-26.3.dist-info/licenses/LICENSE.APACHE` 等 3 个 | 🟢 文本随包 |
| 40 | prometheus_client | 0.26.0 | Apache-2.0 AND BSD-2-Clause | permissive | `prometheus_client-0.26.0.dist-info/licenses/LICENSE`<br>`prometheus_client-0.26.0.dist-info/licenses/NOTICE` | 🟢 文本随包 |
| 41 | propcache | 0.5.2 | Apache-2.0 | permissive | `propcache-0.5.2.dist-info/licenses/LICENSE`<br>`propcache-0.5.2.dist-info/licenses/NOTICE` | 🟢 文本随包 |
| 42 | protobuf | 6.33.6 | BSD-3-Clause | permissive | `protobuf-6.33.6.dist-info/LICENSE` | 🟢 文本随包 |
| 43 | pydantic | 2.13.5 | MIT | permissive | `pydantic-2.13.5.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 44 | pydantic_core | 2.46.5 | MIT | permissive | `pydantic_core-2.46.5.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 45 | Pygments | 2.21.0 | BSD-2-Clause | permissive | `pygments-2.21.0.dist-info/licenses/AUTHORS`<br>`pygments-2.21.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 46 | PyJWT | 2.13.0 | MIT | permissive | `pyjwt-2.13.0.dist-info/licenses/AUTHORS.rst`<br>`pyjwt-2.13.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 47 | pypdf | 6.21.4 | BSD-3-Clause | permissive | `pypdf-6.21.4.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 48 | python-docx | 1.2.0 | MIT | permissive | `python_docx-1.2.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 49 | python-dotenv | 1.2.3 | BSD-3-Clause | permissive | `python_dotenv-1.2.3.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 50 | python-multipart | 0.0.32 | Apache-2.0 | permissive | `python_multipart-0.0.32.dist-info/licenses/LICENSE.txt` | 🟢 文本随包 |
| 51 | pywin32 | 311 | BSD-3-Clause | permissive | `win32/license.txt`<br>`win32com/License.txt` 等 3 个 | 🟢 文本随包 |
| 52 | PyYAML | 6.0.3 | MIT | permissive | `pyyaml-6.0.3.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 53 | requests | 2.34.2 | Apache-2.0 | permissive | `requests-2.34.2.dist-info/licenses/LICENSE`<br>`requests-2.34.2.dist-info/licenses/NOTICE` | 🟢 文本随包 |
| 54 | RestrictedPython | 8.5 | ZPL-2.1 | permissive | `restrictedpython-8.5.dist-info/licenses/LICENSE.txt` | 🟢 文本随包 |
| 55 | rich | 15.0.0 | MIT | permissive | `rich-15.0.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 56 | sniffio | 1.3.1 | MIT OR Apache-2.0 | permissive | `sniffio-1.3.1.dist-info/LICENSE`<br>`sniffio-1.3.1.dist-info/LICENSE.APACHE2` 等 3 个 | 🟢 文本随包 |
| 57 | SQLAlchemy | 2.0.52 | MIT | permissive | `sqlalchemy-2.0.52.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 58 | starlette | 1.6.0 | BSD-3-Clause | permissive | `starlette-1.6.0.dist-info/licenses/LICENSE.md` | 🟢 文本随包 |
| 59 | taskiq | 0.13.0 | MIT | permissive | `taskiq-0.13.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 60 | taskiq-dependencies | 1.5.7 | MIT | permissive | `taskiq_dependencies-1.5.7.dist-info/LICENSE` | 🟢 文本随包 |
| 61 | tenacity | 9.1.4 | Apache-2.0 | permissive | `tenacity-9.1.4.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 62 | truststore | 0.10.4 | MIT | permissive | `truststore-0.10.4.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 63 | typing-inspection | 0.4.4 | MIT | permissive | `typing_inspection-0.4.4.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 64 | typing_extensions | 4.16.0 | PSF-2.0 | permissive | `typing_extensions-4.16.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 65 | urllib3 | 2.8.0 | MIT | permissive | `urllib3-2.8.0.dist-info/licenses/LICENSE.txt` | 🟢 文本随包 |
| 66 | uvicorn | 0.52.4 | BSD-3-Clause | permissive | `uvicorn-0.52.4.dist-info/licenses/LICENSE.md` | 🟢 文本随包 |
| 67 | watchfiles | 1.2.0 | MIT | permissive | `watchfiles-1.2.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 68 | websockets | 16.1.1 | BSD-3-Clause | permissive | `websockets-16.1.1.dist-info/licenses/LICENSE` | 🟢 文本随包 |
| 69 | yarl | 1.25.1 | Apache-2.0 | permissive | `yarl-1.25.1.dist-info/licenses/LICENSE`<br>`yarl-1.25.1.dist-info/licenses/NOTICE` | 🟢 文本随包 |
| 70 | zstandard | 0.25.0 | BSD-3-Clause | permissive | `zstandard-0.25.0.dist-info/licenses/LICENSE` | 🟢 文本随包 |

## 4. 运行时 / 内嵌组件（22 个）

| # | 组件 | 版本 | 许可证 | 归类 | 文本 | 依据 |
|---|---|---|---|---|---|---|
| 1 | bzip2 / libbzip2 | 1.0.8 | bzip2-1.0.8 | permissive | `runtime/LICENSE.txt`（🟢 全文） | runtime/LICENSE.txt 第 326 行起含 bzip2 1.0.8 全文 |
| 2 | CPython | 3.12.14 | PSF-2.0 | permissive | `runtime/LICENSE.txt`（🟢 全文） | runtime/python.exe 的 PE FileVersion；runtime/LICENSE.txt 为 PSF License v2 |
| 3 | httpx-aiohttp（anthropic 1.3.0 内置） | 0.2.0 | BSD-3-Clause | permissive | `runtime/Lib/site-packages/anthropic/_vendor/httpx_aiohttp/LICENSE`（🟢 全文） | NOTICE.md 声明 0.2.0 / BSD-3-Clause，LICENSE 随包 |
| 4 | libexpat（pyexpat.pyd 静态链接） | 2.8.3 | MIT | permissive | `licenses/expat-MIT.txt`（🟢 全文） | pyexpat.EXPAT_VERSION=expat_2.8.3；全文已补齐到 licenses/expat-MIT.txt |
| 5 | libffi | 8（ABI，无 PE 版本资源） | MIT | permissive | `licenses/libffi-MIT.txt`（🟢 全文） | runtime/DLLs/libffi-8.dll 存在；无 FileVersion |
| 6 | liblzma / XZ Utils（_lzma.pyd） | 未标注（随 CPython 3.12.14） | Public-Domain | permissive | —（🟢 公版无需文本） | liblzma 主体为公版；无许可证文本随包，公版不强制署名 |
| 7 | libmpdec（_decimal.pyd） | 2.5.1 | BSD-2-Clause | permissive | `licenses/libmpdec-BSD-2-Clause.txt`（🟢 全文） | decimal.__libmpdec_version__=2.5.1；全文已补齐到 licenses/libmpdec-BSD-2-Clause.txt |
| 8 | libxml2（lxml 内置） | 2.11.9 | MIT | permissive | `runtime/Lib/site-packages/lxml-6.1.3.dist-info/licenses/LICENSES.txt`（🟢 全文） | etree.LIBXML_COMPILED_VERSION 已实测；LICENSES.txt 含 MIT 全文 |
| 9 | libxslt/libexslt（lxml 内置） | 1.1.45 | MIT | permissive | `runtime/Lib/site-packages/lxml-6.1.3.dist-info/licenses/LICENSES.txt`（🟢 全文） | etree.LIBXSLT_COMPILED_VERSION 已实测；LICENSES.txt 含 MIT 全文 |
| 10 | llhttp（aiohttp 3.14.4 内置） | 未标注 | MIT | permissive | `runtime/Lib/site-packages/aiohttp-3.14.4.dist-info/licenses/vendor/llhttp/LICENSE`（🟢 全文） | vendor/llhttp/LICENSE 为 MIT 全文；版本未标注 |
| 11 | MSVC 运行库 vcruntime140/vcruntime140_1 | 14.44.35211.0 | MS-VS-2022-Redistributable | permissive | `licenses/Microsoft-VC-Runtime.txt`（🟢 全文） | PE FileVersion 14.44.35211.0；可再发行组件声明随包 |
| 12 | OpenSSL libcrypto/libssl | 3.5.8 | Apache-2.0 | permissive | `licenses/Apache-2.0.txt`（🟢 全文） | PE FileVersion 3.5.8；ssl.OPENSSL_VERSION=OpenSSL 3.5.8 |
| 13 | SQLite | 3.53.1 | Public-Domain | permissive | `licenses/SQLite-Public-Domain.txt`（🟢 公版无需文本） | sqlite3.sqlite_version 与 PE FileVersion 均为 3.53.1；公版声明随包 |
| 14 | Tcl | 8.6.12 | Tcl/Tk-License | permissive | `licenses/Tcl-Tk-license.terms`<br>`runtime/tcl/tk8.6/license.terms`（🟢 全文） | tcl86t.dll FileVersion=8.6.12；Tcl/Tk license.terms 随包 |
| 15 | Tcl 扩展 tcldde14/tclreg13 | 未标注 | Tcl/Tk-License | permissive | `licenses/Tcl-Tk-license.terms`（🟢 全文） | 随 Tcl 发行版；license.terms 覆盖 |
| 16 | Tix | 8.4.3 | Tix-License | permissive | `licenses/Tix-License.txt`（🟢 全文） | runtime/tcl/tix8.4.3/tix84.dll 随包；全文取自上游 Tix 8.4.3 源码包 license.terms |
| 17 | Tk | 8.6.12 | Tcl/Tk-License | permissive | `licenses/Tcl-Tk-license.terms`<br>`runtime/tcl/tk8.6/license.terms`（🟢 全文） | tk86t.dll FileVersion=8.6.12；license.terms 随包 |
| 18 | Unicode 数据（unicodedata.pyd） | 15.0.0 | Unicode-License | permissive | `licenses/Unicode-License.txt`（🟢 全文） | unicodedata.unidata_version 已实测；全文已补齐到 licenses/Unicode-License.txt |
| 19 | zlib（CPython 静态链接） | 1.3.2 | Zlib | permissive | `licenses/zlib-License.txt`（🟢 全文） | zlib.ZLIB_RUNTIME_VERSION 已实测；全文已补齐到 licenses/zlib-License.txt |
| 20 | zlib（lxml 内置） | 未标注 | Zlib | permissive | `licenses/zlib-License.txt`<br>`runtime/Lib/site-packages/lxml-6.1.3.dist-info/licenses/LICENSES.txt`（🟢 全文） | LICENSES.txt 声明 **zlib**: zlib-license；全文已补齐到 licenses/zlib-License.txt |
| 21 | adodbapi（pywin32 311 内置） | 2.6.2.0 | LGPL-2.1 | weak-copyleft | `licenses/adodbapi-LGPL-2.1.txt`<br>`licenses/LGPL-2.1.txt`<br>`runtime/Lib/site-packages/adodbapi/license.txt`（🟢 全文） | adodbapi/adodbapi.py __version__=2.6.2.0；license.txt 首行为 GNU LGPL v2.1；19 个 .py 源文件随包 |
| 22 | iconv（lxml 6.1.3 二进制 wheel 内置，静态链接） | 未标注（参考上游 libiconv 1.17） | LGPL-2.1 | weak-copyleft | `licenses/LGPL-2.1.txt`<br>`licenses/libiconv-NOTICE.txt`<br>`licenses/LGPL-SOURCE-OFFER.txt`（🟢 全文） | LICENSES.txt 声明 **iconv**: LGPL 2.1；etree.pyd 内含 libiconv 字符串，随包无 libiconv DLL，判定静态链接；LGPL 全文 + 源码要约 + 重链接步骤已随包 |

## 5. 前端 bundle 依赖（3 个）

| # | 名称 | 版本 | 声明许可证 | 归类 | 许可证文本 | 状态 |
|---|---|---|---|---|---|---|
| 1 | react | 19.2.8 | MIT | permissive | `licenses/React-MIT.txt` | 🟢 文本随包 |
| 2 | react-dom | 19.2.8 | MIT | permissive | `licenses/React-MIT.txt` | 🟢 文本随包 |
| 3 | scheduler | 0.27.0 | MIT | permissive | `licenses/React-MIT.txt` | 🟢 文本随包 |

## 6. 与 THIRD-PARTY-NOTICES 的一致性核对

- 🟢 安装树 `THIRD-PARTY-NOTICES.txt` 与 `packaging/third_party_notices.py` 现场生成结果**逐字节一致**。
- 🟢 数量：清单声明 70 个 / 表格 70 行 / 实装 70 个。
- 🟢 名称与版本集合一致，无缺项/多项/版本漂移。
- 🟢 清单中所有 `许可证文本随包位置` 均真实存在。
- 🟢 限制性许可证表述如实：顶层 0 个、嵌入层 2 个 LGPL-2.1（adodbapi（pywin32 311 内置）（LGPL-2.1）、iconv（lxml 6.1.3 二进制 wheel 内置，静态链接）（LGPL-2.1）），并指向第 3.1 节义务处置。

## 7. 许可证文本缺失/不完整清单

- 无

> pywin32 的 `dist-info` 内没有 LICENSE 文件，但 `win32/license.txt`、`win32com/License.txt`、
> `pythonwin/license.txt` 随包，且 `licenses/pywin32-BSD-3-Clause.txt` 有副本，因此 pywin32 的
> **BSD-3-Clause 主许可证文本不缺失**；其内置 `adodbapi` 的 LGPL-2.1 已单独列示（3.1.1 节）。

## 8. 需补齐的动作（按优先级）

1. **法务书面确认（唯一的非材料项）**：确认 iconv 静态链接方案（LGPL 全文 + 源码要约 + 可重链接步骤，见 `licenses/LGPL-SOURCE-OFFER.txt`）满足 LGPL-2.1 第 6 条；adodbapi 为纯 Python 且源码随包，无静态链接问题。若法务不接受静态链接，具体改哪一行、验证什么见 `dist/release-gate/LGPL-ICONV-DYNAMIC-LINK-PLAN.md`（buildlibxml.py / setupinfo.py / setup.py 三处改动 + PE 导入表与 DLL 验证）。
2. **发布流程**：把 `dist/release-gate/lgpl-sources/`（lxml 6.1.3 与 libiconv 1.17 源码包）随发布交付物一并归档，并保留至少 3 年，以兑现 `licenses/LGPL-SOURCE-OFFER.txt` 的书面要约。责任人、归档位置、保留期与每季度 SHA256 抽检见 `dist/release-gate/RELEASE-RUNBOOK.md` 第 9.2 节。
3. **成品包校验**：每个安装器与 payload 转正前各跑一次 `dist/release-gate/verify_package_compliance.py`（`--exe` 或 `--payload`），输出许可证文本 / SBOM 覆盖 / 缺项；PASS 才允许转正，见 `RELEASE-RUNBOOK.md` 第 9.1 节。

## 9. 复核方式

```powershell
# 1) 重新生成 SBOM、证据与报告（只读被审计目录）
D:\LiuHao-AI-OS\.venv\Scripts\python.exe D:\LiuHao-AI-OS\dist\release-gate\sbom_audit.py --app "D:\LiuHao-AI-OS\dist\payload\app"

# 2) 单独核对 THIRD-PARTY-NOTICES 是否仍与实装一致
D:\LiuHao-AI-OS\.venv\Scripts\python.exe D:\LiuHao-AI-OS\packaging\third_party_notices.py `
  --app "D:\LiuHao-AI-OS\dist\payload\app" --check "D:\LiuHao-AI-OS\dist\payload\app\THIRD-PARTY-NOTICES.txt"

# 2b) 构建门禁：清单 ↔ 实装 ↔ 许可证文本三方一致（不一致退出码 1）
D:\LiuHao-AI-OS\.venv\Scripts\python.exe D:\LiuHao-AI-OS\dist\release-gate\sbom_audit.py `
  --app "D:\LiuHao-AI-OS\dist\payload\app" --check

# 2c) 成品包校验：许可证文本 / SBOM 覆盖 / 缺项（exe 或 payload 均可）
D:\LiuHao-AI-OS\.venv\Scripts\python.exe D:\LiuHao-AI-OS\dist\release-gate\verify_package_compliance.py --payload "D:\LiuHao-AI-OS\dist\payload\app"
# 安装器：把 --payload 换成 --exe "<LiuHao-Setup-版本.exe>"；只读解包，不执行 setup.exe

# 3) 查看 SBOM 组件总数
D:\LiuHao-AI-OS\.venv\Scripts\python.exe -c "import json;d=json.load(open(r'D:\LiuHao-AI-OS\dist\release-gate\sbom-cyclonedx.json',encoding='utf-8'));print(len(d['components']))"
```

## 10. 口径与限制

1. 本报告是**工程与合规筛查**，不是法律意见；LGPL 静态链接方案与 MSVC 可再发行条款需法务最终确认。
2. `lxml` 的 `LICENSES.txt` 声明二进制 wheel 内置 `zlib/iconv/libxml2/libxslt/libexslt`；`etree.pyd` 内检出
   libiconv 字符串且随包无 `libiconv` DLL，判定 iconv 为静态链接。已按 LGPL-2.1 随包全文、源码要约与重链接步骤，
   该方案是否满足第 6 条需法务书面确认。
3. `Tix` 为 CPython 安装器附带的 Tcl 扩展，采用 BSD 风格 Tix License；全文已随包，产品运行时不加载。
4. 本报告未把 Windows 系统 DLL、Inno Setup 编译器本身计入第三方组件；Inno Setup 编译产物可免版税分发，但不在本清单覆盖范围。
