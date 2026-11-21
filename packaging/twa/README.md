# Jarvis 安卓 App（TWA）打包套件 · 操作说明

> 这份套件把已经做好的贾维斯网页版（PWA）包成一个**真正的安卓 App**：
> 手机桌面上有小铁图标、点开全屏没有浏览器地址栏、能像普通 App 一样安装。
> 技术上叫 TWA（Trusted Web Activity），用的是 Google 官方的 Bubblewrap 工具
> （Apache-2.0 开源）。App 里装的还是贾维斯本体，不存在第二套界面要维护。
>
> **整个流程在你的 Windows 电脑上做**，大约 20–40 分钟（第一次慢些）。
> 凡是让你输密码的地方，都是你本人在工具的提示里输——密码不经过任何脚本、
> 不存进任何文件。遇到报错，把报错原样发给 Muse，不要删文件重来。

## 第 0 步：先想清楚 App 打开的是哪个地址

TWA 有一条硬规矩：App 打开的网址必须是 **HTTPS 域名地址**，
局域网里那种 `http://192.168.x.x:端口` 不行。所以打包之前，
先给贾维斯准备一个 HTTPS 入口，二选一：

- **推荐（不出家门）**：电脑和手机都装 Tailscale（免费档够用），在电脑上执行
  `tailscale serve` 把贾维斯网关挂到 `https://<你的机器名>.<你的 tailnet 名>.ts.net`。
  这个地址只有你自己的设备能访问，证书是现成的。装机清单里已有这条路的说明。
- **或**：你有自己的域名和公网服务器，把域名反向代理到家里电脑的贾维斯网关。

下文把这个地址记作 `https://你的地址`。先在手机浏览器里打开它，
确认贾维斯能正常登录使用，再往下走。

## 第 1 步：装 Node.js

到 https://nodejs.org 下载 **LTS 版**安装（一路默认即可）。
装完新开一个 PowerShell，输入 `node --version` 能显示版本号就行。
JDK 和安卓工具链**不用自己装**——Bubblewrap 第一次运行时会自备，
放在 `C:\Users\<你>\.bubblewrap` 里。

## 第 2 步：把套件放到电脑上，改两处地址

把源码里的 `packaging\twa` 整个文件夹复制到电脑上（比如 `D:\Jarvis-TWA`）。
用记事本打开里面的 `twa-manifest.json`，只改两处：

- `"host"`: `your-jarvis-host.example.com` → 改成你的地址（**只写域名，不要带 https://**）
- `"webManifestUrl"`: 改成 `https://你的地址/manifest.webmanifest`

其余字段都已按贾维斯的现成配置填好（图标、配色、名字都是现成的）。
**特别注意：`packageId`（`com.liuhaoglobal.jarvis`）一旦定下永远不要改**，
改了安卓就认为是另一个 App，老用户无法升级。

## 第 3 步：一键打包

在本文件夹空白处 Shift+右键 →「在此处打开 PowerShell」（或先开 PowerShell 再 `cd` 进来），执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build-twa.ps1
```

脚本会自动检查环境、生成安卓工程、打包。中途 Bubblewrap 可能让你确认
下载它自备的 JDK/安卓工具，同意即可。产物在本文件夹：

- `app-release-signed.apk` —— 侧载安装包，传到手机点开就能装
- `app-release-bundle.aab` —— 将来上架 Google Play 用的包

## 第 4 步：签名密钥——生成与保管（最重要，别跳过）

第一次打包时，如果还没有签名密钥，Bubblewrap 会现场引导生成
`android.keystore`：让你填姓名/单位（填真实信息即可）和**密钥密码**。

**这个文件就是这个 App 的命根子**，像公司印章一样管：

- 安卓只认同一把密钥签名的升级包。**keystore 丢了、或密码忘了，
  这个 App 就永远无法再更新**，只能换个新 App 重来。
- 打包一完成，立刻备份：①一份存进你的密码管理器（文件+密码一起）；
  ②一份存到离线介质（U 盘）放好。两份不要只放在同一台电脑上。
- **永远不要**把 keystore 发给任何人、不要传到公开网盘、不要放进代码仓库
  （套件目录已设了防误传规则，但自己也别手动往外拷）。
- 以后换电脑打包，把这份 keystore 拷回新电脑的套件目录里，接着用同一把。

## 第 5 步：取指纹、部署 assetlinks.json（做完才算真 App）

这一步是告诉安卓：「这个网址确实是这个 App 的」。不做，App 打开时
顶部会留着浏览器地址栏（那叫 Custom Tab，不算数）；做对了，才是全屏真 App。

1. 取签名指纹（PowerShell，在本文件夹执行；会让你输第 4 步的密钥密码）：

   ```powershell
   & "$env:USERPROFILE\.bubblewrap\jdk\*\bin\keytool.exe" -list -v -keystore .\android.keystore -alias android
   ```

   （若路径对不上，用 Bubblewrap 自备 JDK 的实际位置；或者装了 Java 的话直接 `keytool`。）
   在输出里找 `SHA256:` 那一行，一长串冒号分隔的十六进制，就是指纹。

2. 打开套件里的 `assetlinks.json`，把 `REPLACE_WITH_YOUR_KEYSTORE_SHA256_FINGERPRINT`
   换成这串指纹（冒号保留）。

3. 把这份 `assetlinks.json` 放到你的地址根目录下这个固定位置：
   `https://你的地址/.well-known/assetlinks.json`。
   若你的入口就是贾维斯本体（Tailscale Serve 挂的网关），把文件复制到
   **安装目录的 `console\.well-known\` 文件夹**里（没有就新建），重启贾维斯即可。

4. 自查：浏览器打开 `https://你的地址/.well-known/assetlinks.json`，
   能打开、内容就是你填的那份，才算部署成功。生效可能要等几分钟到几小时
   （安卓有缓存），App 里地址栏消失 = 生效了。

## 第 6 步：真机安装验收

把 `app-release-signed.apk` 传到安卓手机（微信/数据线/U盘都行），点开安装
（系统会提示允许「安装未知应用」，允许这一次）。逐项核对：

- [ ] 桌面图标是**小铁**，名字是「贾维斯」
- [ ] 点开是全屏，**没有浏览器地址栏**（有 = 第 5 步没生效，回去查）
- [ ] 能登录、能正常聊天一轮
- [ ] 关掉电脑上的贾维斯（或断网）后重开 App，有明确的连不上提示，不白屏卡死
- [ ] 手机上卸载后重装一次，能正常装回（确认安装包本身没问题）

## 第 7 步（可选）：上架 Google Play

- 需要 Google Play 开发者账号：**25 美元一次性**——这是花钱项，你点头才办。
- 用第 3 步产出的 `.aab` 上传。若启用 Google 的应用签名，Google 会再给
  一串「应用签名密钥」的 SHA-256 指纹：把那串也**追加**进 `assetlinks.json`
  的指纹数组（两个都留），重新部署第 5 步的文件即可。
- 只自己用/侧载的话，这一步永远可以不做。

## 常见问题

- **脚本说 manifest 打不开**：先在浏览器确认 `https://你的地址/manifest.webmanifest`
  能打开。TWA 打包要从这个地址下载图标，地址不通就打不了包。
- **打包中途失败**：不要删任何已生成的文件；把报错整段发给 Muse，
  多数是网络或地址问题，改对后重跑脚本即可（已生成的工程会复用）。
- **地址栏还在**：99% 是第 5 步——指纹抄错、文件没放对位置、或缓存没刷新。
  先自查第 5 步的第 4 点，再把 App 卸载重装一次。
- **换了 HTTPS 地址**：改 `twa-manifest.json` 的两处地址，重跑脚本重新打包，
  并把 `assetlinks.json` 部署到新地址的 `.well-known` 下。密钥不用换。

## 如实说明（边界）

- 本套件是在 Linux 构建机上配好、验过配置合法性的；构建机上**没有 JDK/安卓
  工具链，无法在本机实跑出包**。Windows 上的第一次打包就是实跑验证轮：
  出包成功 + 第 6 步清单全过，才算 V3 真机验收完成。
- 安卓手表、iOS 原生 App 不在本套件范围（iPhone/iPad 继续用 PWA
  「添加到主屏幕」，已就绪）。
