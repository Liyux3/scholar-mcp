<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/scholar-mcp-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/scholar-mcp-logo.svg">
    <img alt="Scholar MCP" src="docs/assets/scholar-mcp-logo.svg" width="820">
  </picture>
</p>

<p align="center">
  <a href="https://vscode.dev/redirect/mcp/install?name=scholar-mcp&amp;config=%7B%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--python%22%2C%223.12%22%2C%22--find-links%22%2C%22https%3A%2F%2Fgithub.com%2FLiyux3%2Fscholar-mcp%2Freleases%2Fdownload%2Fv0.9.0%2Fwheel-index.html%22%2C%22--from%22%2C%22scholar-mcp%5Brerank%5D%3D%3D0.9.0%22%2C%22scholar-mcp%22%5D%2C%22env%22%3A%7B%22SCHOLAR_MCP_EXTENSIONS%22%3A%22research%22%7D%7D"><img src="https://img.shields.io/badge/Install_in-VS_Code-53665B.svg?style=flat-square" alt="Install in VS Code"></a>
  <a href="cursor://anysphere.cursor-deeplink/mcp/install?name=scholar&amp;config=eyJzY2hvbGFyIjp7ImNvbW1hbmQiOiJ1dngiLCJhcmdzIjpbIi0tcHl0aG9uIiwiMy4xMiIsIi0tZmluZC1saW5rcyIsImh0dHBzOi8vZ2l0aHViLmNvbS9MaXl1eDMvc2Nob2xhci1tY3AvcmVsZWFzZXMvZG93bmxvYWQvdjAuOS4wL3doZWVsLWluZGV4Lmh0bWwiLCItLWZyb20iLCJzY2hvbGFyLW1jcFtyZXJhbmtdPT0wLjkuMCIsInNjaG9sYXItbWNwIl0sImVudiI6eyJTQ0hPTEFSX01DUF9FWFRFTlNJT05TIjoicmVzZWFyY2gifX19"><img src="https://img.shields.io/badge/Add_to-Cursor-6A3A3D.svg?style=flat-square" alt="Add to Cursor"></a>
  <a href="https://kiro.dev/launch/mcp/add?name=scholar-mcp&amp;config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--python%22%2C%223.12%22%2C%22--find-links%22%2C%22https%3A%2F%2Fgithub.com%2FLiyux3%2Fscholar-mcp%2Freleases%2Fdownload%2Fv0.9.0%2Fwheel-index.html%22%2C%22--from%22%2C%22scholar-mcp%5Brerank%5D%3D%3D0.9.0%22%2C%22scholar-mcp%22%5D%2C%22disabled%22%3Afalse%2C%22autoApprove%22%3A%5B%5D%2C%22env%22%3A%7B%22SCHOLAR_MCP_EXTENSIONS%22%3A%22research%22%7D%7D"><img src="https://img.shields.io/badge/Add_to-Kiro-8C714C.svg?style=flat-square" alt="Add to Kiro"></a>
  <a href="https://registry.modelcontextprotocol.io/?search=io.github.Liyux3%2Fscholar-mcp"><img src="https://img.shields.io/badge/MCP_Registry-Scholar-3D5946.svg?style=flat-square" alt="MCP Registry"></a>
  <a href="https://pypi.org/project/scholar-mcp"><img src="https://img.shields.io/pypi/v/scholar-mcp.svg?style=flat-square" alt="PyPI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache_2.0-3D5946.svg?style=flat-square" alt="Apache 2.0"></a>
</p>

<p align="center">
  找到论文，顺着证据，理清领域。<br>
  <a href="https://liyux3.github.io/scholar-mcp/zh-cn/">文档</a> · <a href="README.md">English</a> · 简体中文
</p>

Scholar MCP 给你的 AI 智能体配上一座研究图书馆。不用密钥时它同时搜索 17 个学术数据源，加上你自己的密钥最多 24 个。它会顺着引用和相关工作继续找，读原文一直读到图和表，再把要紧的论文连同你的笔记存进本地文献库。Claude、Codex、Cursor、VS Code、Gemini CLI、OpenCode 等任何 MCP 客户端都能用，命令行和 Python 里也能用。

<p align="center">
  <a href="https://liyux3.github.io/scholar-mcp/zh-cn/#watch"><img src="docs/assets/scholar-film-poster.jpg" alt="观看一分钟介绍片" width="820"></a>
</p>

## 📰 动态

- 2026 年 10 月，0.9.0。`scholar-mcp install` 一条命令装进 27 个智能体，一份密钥文件所有客户端共用，每个数据源在自己的车道上并行搜索，`scholar-mcp doctor --network` 检查每条链路。
- 2026 年 10 月，0.8.5。Google Scholar 会话可以自动恢复，arXiv 接口不可用时改用它自己的网页搜索。

## 🚀 开始使用

1. 没有 [uv](https://docs.astral.sh/uv/getting-started/installation/) 的话先装上，然后安装 Scholar。

   ```bash
   uv tool install --python 3.12 --find-links https://github.com/Liyux3/scholar-mcp/releases/latest/download/wheel-index.html "scholar-mcp[rerank]"
   ```

2. 接入你的智能体。Scholar 会找出本机装了哪些客户端，把自己注册进去，改动的每个文件都先备份。

   ```bash
   scholar-mcp install --all
   ```

   安装命令不认识的客户端，让智能体按 [llms-install.md](llms-install.md) 自己装即可。也可以点名要装的客户端（`scholar-mcp install claude-code codex`），加 `--dry-run` 先看会改什么，加 `--profile research` 带上引用图谱和文献库。之后运行 `scholar-mcp update`，所有已接入的客户端用的命令一起升级。用 uvx 固定了版本的客户端，运行 `uvx scholar-mcp@latest install 客户端名` 刷新。

3. 重启客户端，提一个研究问题，比如“找一些评估 LLM 智能体长期记忆的近期工作，读一下引用最多的那篇，比较它用到的基准测试”。

<details>
<summary>其他安装方式</summary>

任何 stdio MCP 客户端都能直接通过 uvx 启动 Scholar，不用提前安装。

```json
{
  "mcpServers": {
    "scholar": {
      "command": "uvx",
      "args": [
        "--python", "3.12",
        "--find-links", "https://github.com/Liyux3/scholar-mcp/releases/latest/download/wheel-index.html",
        "--from", "scholar-mcp[rerank]", "scholar-mcp"
      ]
    }
  }
}
```

Claude Code 对应的命令是 `claude mcp add scholar -- uvx --python 3.12 --find-links <index> --from 'scholar-mcp[rerank]' scholar-mcp`。

这个仓库同时也是 Codex 和 Claude Code 的插件市场，插件带有研究工具和 Deep Research 技能。

```bash
codex plugin marketplace add Liyux3/scholar-mcp && codex plugin add scholar-mcp@scholar-mcp
claude plugin marketplace add Liyux3/scholar-mcp && claude plugin install scholar-mcp@scholar-mcp
```

每个版本还提供自带 Python 的 macOS 桌面安装包（MCPB）、多架构容器镜像 `ghcr.io/liyux3/scholar-mcp`，以及 [MCP Registry](https://registry.modelcontextprotocol.io/?search=io.github.Liyux3%2Fscholar-mcp) 上的条目。见[分发说明](docs/DISTRIBUTION.md)，以及给智能体自己安装用的 [llms-install.md](llms-install.md)。

</details>

## 🧰 智能体能做什么

| | 工具 | |
|---|---|---|
| 搜索 | `search_papers` | 一个问题同时发给所有数据源，重复的记录合并成一条，整体排序后顺着最相关论文的引用再找一轮，最后统一排序 |
| 查看 | `paper_info` | 一次拿到一篇论文的完整信息、被引和参考文献 |
| 拓展 | `recommend_papers` | 内容相近的论文、常被一起引用的论文，以及引用同一批文献的论文 |
| 学者 | `search_authors` | 作者资料、所属机构和学术指标 |
| 阅读 | `read_paper` | 按页标注的 Markdown、结构化表格，以及任意一张图的截图，读完即删 |
| 保存 | `download_paper` | 从开放存档、仓库或你学校的图书馆拿到 PDF，存进文献库 |
| 图谱 | `build_paper_graph` | 带 PageRank 和桥接论文的引用图谱（研究工具集） |
| 文献库 | `paper_library` | 集合、笔记、标签和全文搜索，可同步到 Obsidian、Zotero 或 Notion（研究工具集） |

Scholar 给出的任何标识符、论文链接或准确标题，都能传给任何一个工具。每次返回一份给模型读的 YAML 和一份给客户端用的结构化数据，`scholar://status` 会告诉你正在用哪些数据源和哪个排序模型。

## 🧭 工作原理

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/retrieval-pipeline-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/retrieval-pipeline.svg">
  <img alt="Scholar MCP 检索流程" src="docs/assets/retrieval-pipeline.svg">
</picture>

语义数据源收到完整的问题，关键词数据源收到按各自实测效果截取的关键词。每个数据源在自己的车道上按它允许的节奏回答，赶不上第一轮排序的，也会在最终排序前并进来。OpenAlex、Semantic Scholar、arXiv、Crossref、OpenReview、PubMed、Europe PMC、DBLP、zbMATH、CORE、OpenAIRE、Google Scholar 等来源的记录按共同的 DOI、arXiv 编号和标题合并，每篇论文只出现一次，信息互相补全。排序模型（云端的 Qwen3 Rerank，或本地模型）给候选池排序，最相关的几篇再带出它们的被引和参考文献，合在一起再排一次。[架构页](https://liyux3.github.io/scholar-mcp/zh-cn/architecture/)逐步讲解每个环节，[后端短片](https://liyux3.github.io/scholar-mcp/zh-cn/architecture/#film)用两分半钟带一个问题走完全程。

<details>
<summary>运行时架构</summary>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/runtime-architecture-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/runtime-architecture.svg">
  <img alt="Scholar MCP 运行时架构" src="docs/assets/runtime-architecture.svg">
</picture>

智能体通过 stdio 或 Streamable HTTP 调用带类型的 MCP 工具。全文优先取开放副本，学校订阅的论文经你的图书馆代理获取。一个带 FTS5 全文搜索的 SQLite 文献库保存论文、笔记和 PDF 附件，位于 `~/.scholar-mcp/`，并能导出到 Obsidian、同步到 Zotero 和 Notion。

</details>

## 📊 检索效果

![LitSearch 检索效果对比](docs/assets/litsearch-quality.svg)

在同样 50 个 LitSearch 问题上，Scholar 在前五条结果里找到正确论文的比例是 62%，Exa 论文搜索是 52%，BM25 是 46%。

<details>
<summary>结果和测试方法</summary>

| 系统 | Hit@5 | Hit@10 | Hit@20 | MRR |
|---|---:|---:|---:|---:|
| Scholar | 0.62 | 0.68 | 0.70 | 0.442 |
| Exa `research paper` | 0.52 | 0.58 | 0.64 | 0.435 |
| BM25 `title + abstract` | 0.46 | 0.46 | 0.56 | 0.335 |

Hit@k 是前 k 条里至少有一篇标准答案的问题所占比例，MRR 是第一条正确结果排名倒数的平均值。Scholar 有九个问题的前五条命中而 Exa 没有，反过来有四个。对比使用 LitSearch inline-ACL 的前 50 个问题、同一个标题匹配规则和前 20 条截断。Scholar 跑的是标准流程加 Qwen 排序，Exa 用 `research paper` 类别，BM25 按 LitSearch 官方的标题加摘要实现，在 6.4 万篇的语料上检索。冻结的汇总在 [`docs/benchmarks/litsearch-inline-acl-50.json`](docs/benchmarks/litsearch-inline-acl-50.json)，另有 [BM25 原始结果](docs/benchmarks/bm25_title_abstract_inline_acl_50.jsonl)和[哈希清单](docs/benchmarks/bm25_title_abstract_inline_acl_50.summary.json)。

</details>

## 📚 你的文献库

![真实文献库生成的引用图谱](docs/assets/paper-library-graph.svg)

存下的论文、PDF 和笔记都在同一个本地 SQLite 文献库里，跨会话可搜。Obsidian 会收到实时更新的 Markdown，Zotero 是同步的分类，Notion 是一份阅读清单。你在 Obsidian 或 Notion 里改过的笔记会被读回来，智能体接着你的修改继续做。

```bash
scholar-mcp library export obsidian --collection memory --path ~/Vault
scholar-mcp library sync zotero --collection memory --apply
```

## 💻 命令行和 Python

不用智能体也能直接用这些工具。默认输出易读的 YAML，加 `--json` 输出 JSON。

```bash
scholar-mcp search "long-term memory benchmarks for LLM agents" --year 2024-2026
scholar-mcp read arXiv:2504.19413 --pages 1-4
scholar-mcp read arXiv:2504.19413 --visual "Figure 1" --image figure1.png
scholar-mcp doctor --network
```

```python
from scholar_mcp import api

hits = api.search("long-term memory benchmarks for LLM agents", year="2024-2026")
paper = api.info(hits["results"][0]["id"], include="detail,citations")
```

## 🔑 配置

不设任何密钥也能用，可选的密钥能扩大覆盖、提高限额。`scholar-mcp keys set 名称` 把密钥保存到 `~/.scholar-mcp/keys.env`，所有客户端都会读到。写在客户端 `env` 或 shell 里的同名变量也有效，并且优先。

| 变量 | 作用 |
|---|---|
| `S2_API_KEY` | 提高 Semantic Scholar 的限额，打开全文片段搜索和推荐 |
| `OPENALEX_API_KEY`、`OPENALEX_EMAIL` | OpenAlex 的语义搜索和更宽的限额，邮箱还用于 Unpaywall |
| `DASHSCOPE_API_KEY` | 用云端的 Qwen3 Rerank 排序，不设时由本地模型排序 |
| `SCHOLAR_MCP_EXTENSIONS=research` | 加上引用图谱和文献库 |
| `SCHOLAR_DATA_DIR` | 文献库、PDF 和状态的位置，默认 `~/.scholar-mcp` |

全部变量见[配置文档](https://liyux3.github.io/scholar-mcp/zh-cn/configuration/)，包括 Scopus、CORE、Exa 和四个按密钥启用的数据源（NASA ADS、Springer Nature、Web of Science Starter、Lens）、自定义排序服务、图书馆代理、Zotero 和 Notion。

<details>
<summary>付费墙论文和学校图书馆</summary>

`download_paper` 和 `read_paper` 并行寻找开放副本，并优先保留到手的出版商正式版。需要学校订阅的论文时，在服务器环境中设置学校的 EZproxy 前缀，以及从已登录浏览器导出的 Netscape cookies.txt 文件。

```bash
export LIBRARY_PROXY_PREFIX="https://proxy.library.example.edu/login?url="
export LIBRARY_PROXY_COOKIES="$HOME/private/library-cookies.txt"
```

Scholar 只读取该代理域内的 Cookie。会话过期后，在浏览器中重新登录并导出新文件即可。客户端配置与网络路径见[机构访问](https://liyux3.github.io/scholar-mcp/zh-cn/institutional-access/)。

</details>

<details>
<summary>Google Scholar</summary>

装好 Chrome、Edge 或 Chromium 和 ffmpeg 后，用 `scholar-mcp[google,rerank]` 即可自动恢复验证。一个使用全新配置的临时无窗口浏览器建立会话，之后的搜索和翻页走普通 HTTP。会话私密地保存在 `<data>/sessions/` 下。设置 `SCHOLAR_GOOGLE_RECOVERY=off` 可关闭。

</details>

## 🔧 开发

```bash
git clone https://github.com/Liyux3/scholar-mcp.git
cd scholar-mcp
uv sync --extra dev
uv run pytest            # 单元测试，真实接口测试用 -m integration
```

新数据源只是 `scholar_mcp/sources/` 里的一个模块加一条登记，不需要新的工具。`scholar-mcp doctor --network` 会探测每个数据源、排序模型和 PDF 链路，CI 每天也会跑一遍。各部分如何配合见 [ARCHITECTURE.md](docs/ARCHITECTURE.md)，贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题请按 [SECURITY.md](SECURITY.md) 报告。

## 📝 引用

如果 Scholar MCP 对你的研究有帮助，欢迎引用。

```bibtex
@software{li_scholar_mcp_2026,
  author  = {Li, Yuxian},
  title   = {Scholar MCP},
  year    = {2026},
  version = {0.9.0},
  url     = {https://github.com/Liyux3/scholar-mcp},
  license = {Apache-2.0}
}
```

## 📄 许可证

Apache License 2.0。欢迎复用，复用时请保留 [NOTICE](NOTICE) 文件，并在你的项目中注明来自 Scholar MCP，无论是人还是 AI 智能体完成的复制。

安装包中附带的 CPU 阅读模型来自 [prithivida/FlashRank](https://huggingface.co/prithivida/flashrank)，按 CC-BY-SA-4.0 原样分发并保留独立署名说明。
