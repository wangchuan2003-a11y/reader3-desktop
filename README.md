# Reader3 Desktop

基于 [Andrej Karpathy 的 reader3](https://github.com/karpathy/reader3) 改造的中文 EPUB 阅读器。按章节阅读，复制整章或所选文字，再到自己常用的 AI 中讨论。

## 功能

- 本机导入 EPUB、书名／作者搜索、最近阅读排序。
- 章节目录、长文滚动、脚注跳转；章节边界无法可靠识别时明确提示范围。
- 整章与选中文字复制，附带书名、作者、章节和范围标记。
- 字号、行距、正文宽度和浅色／暖色／深色主题。
- 本机 SQLite 阅读进度；同一书库的网页与桌面入口共享位置。
- macOS 原生窗口、系统文件选择、菜单、剪贴板和独立应用打包。

不需要 AI API 密钥。阅读器没有内置 AI 对话，也不会自动向 AI 发送书籍。项目不包含个人 EPUB、阅读记录或账户信息。

## 从源码运行

网页功能要求 Python 3.10 及以上；下面使用 Python 3.12，以便同时构建桌面版。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-local.txt
.venv/bin/python server.py
```

打开 <http://127.0.0.1:8123/>，在书架导入 EPUB。默认使用项目目录作为书库；可以通过 `READER3_LIBRARY_DIR` 指定另一位置。服务只监听本机。

如需与已安装的桌面版共用书库，先关闭桌面版，再运行：

```sh
READER3_LIBRARY_DIR="$HOME/Library/Application Support/Reader3" .venv/bin/python server.py
```

已有合适的本机服务时，桌面版会复用它。端口被其他服务占用时会提示，不会擅自结束其他程序。

## 构建 Mac 桌面版

目前面向 Apple silicon，构建目标 macOS 13+。需要 Xcode Command Line Tools、Python 3.12 和上面配置好的 `.venv`。

```sh
desktop/build.sh
```

结果为 `dist/Reader3.app`。将整个应用复制到自己的应用程序文件夹即可使用。包内包含运行环境；书库默认位于 `~/Library/Application Support/Reader3`，不会装入应用包。

构建脚本检查内置运行环境的动态库路径及搬移后运行能力。不同 Python 发行版的布局可能不同；存在包外动态库依赖时会拒绝打包。已验证的构建环境使用 Apple silicon Python 3.12，其他环境需自行验收。

当前使用本机临时签名，尚未完成 Developer ID 签名、公证或其他电脑兼容性验收。源代码发布不代表已有经过公证的安装包。

## 检查与演示

```sh
.venv/bin/python -m unittest discover -s tests
node --test tests/*.test.cjs
```

测试使用合成 EPUB 与临时目录。可以自行生成项目原创的演示书：

```sh
.venv/bin/python examples/create_demo.py
```

然后在书架导入生成的 `demo.epub`。验收记录见 [VALIDATION.md](VALIDATION.md)。

## 使用边界

- 图片与复杂公式不自动变成可复制文字；AI 的输入长度限制需要在目标 AI 中确认。
- 阅读进度只在同一台电脑、同一书库内共享，没有账号和跨设备同步。
- 显示偏好分别保存在各个浏览器／桌面窗口的数据空间。
- 备份时先退出阅读器并停止另行启动的服务，再复制整个书库。

## 上游与许可

基线为 `karpathy/reader3` 的提交 `64960f99d8d3c2eaaec56c6765b4c1aeae14c80b`。原版说明及 MIT 许可声明保存在 [UPSTREAM-README.md](UPSTREAM-README.md)。本仓库不是 Karpathy 的官方桌面版本；桌面封装、中文阅读设置、导入校验和持久进度是后续改动。
