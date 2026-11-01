<div align="center">

<img src="docs/images/logo.svg" alt="Decision Forge logo" width="88">

# Decision Forge

**把组织制度编译成可验证的决策训练数据。**

[![CI](https://github.com/Juneo-Lab/decision-forge/actions/workflows/ci.yml/badge.svg)](https://github.com/Juneo-Lab/decision-forge/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Juneo-Lab/decision-forge/actions/workflows/codeql.yml/badge.svg)](https://github.com/Juneo-Lab/decision-forge/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/Juneo-Lab/decision-forge?include_prereleases&color=FF5A1F)](https://github.com/Juneo-Lab/decision-forge/releases)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-3776ab)](pyproject.toml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Typed](https://img.shields.io/badge/typing-strict%20mypy-2a6db2)](pyproject.toml)

[快速开始](#快速开始) ·
[工作原理](#工作原理) ·
[文档](docs/index.md) ·
[路线图](ROADMAP.md) ·
[参与贡献](CONTRIBUTING.md) ·
[变更记录](CHANGELOG.md)

[English](README.md) · **简体中文**

</div>

![Decision Forge：编译与审核组织知识，按四类决策与场景分布构造样本，通过规则重放验证标签，导出训练数据。](docs/assets/decision-forge-overview-v2.png)

组织决策依赖手册、SOP 和流程定义中的政策、阈值、升级规则与验收标准。Decision Forge 将这些知识
转化为可复现的决策模型监督数据。

文档编译器生成带有源文引证的规则规格，供业务人员审核。数据管线依据审核后的规则评估场景，导出带有
已验证标签、来源记录和稳定场景族分配的决策样本。这些训练数据可以审计、复现，并交给训练器使用。

> [!NOTE]
> **状态：`v0.3.0` alpha — 组织决策数据管线。**
> 已审核或手工编写的规格可以在本地构建训练数据，无需模型凭证。文档编译与模型辅助合成使用显式指定的
> 模型端点。当前实现与后续里程碑见[项目状态](docs/status.md)。

## 核心特性

| 能力 | 说明 |
| --- | --- |
| **离线数据路径** | 已审核规则 → 场景 → 已验证标签 → 导出，无需 API key 或下载模型 |
| **规则验证的标签** | 由确定性解释器根据声明规则重新计算 |
| **来源可追溯** | 验证规则引文和源文件哈希；语料编译补充跨度与定位符 |
| **防泄漏切分** | 相关变体共享场景族切分；跨切分重复会被报告并剔除 |
| **可直接训练的导出** | 四种格式，包括 candidate-readout 决策契约 |
| **广泛的文档接入** | 文本、Markdown、JSON/YAML、PDF、DOCX/XLSX/PPTX、HTML、CSV/TSV 与本地 OCR |
| **验证** | 完整可选适配器环境下，280 项测试、88.41% 语句覆盖率 |
| **持续检查** | Linux 与 macOS 上共 10 项 CI 任务，包括适配器、OCR 与 Harbor 对照验证；另有 CodeQL 与 OpenSSF Scorecard 扫描 |

## 工作原理

`compile-corpus` 通过显式指定的模型端点从组织源材料起草规则，经业务审核接受；手工编写的规格可跳过这两步。
审核之后的步骤全部离线完成：场景族与配额、通过规则解释器重放标签、去重与场景族切分隔离，以及训练导出。

管线将工作流中的决策节点组织为四类：

| 决策面 | 模型要回答的问题 |
| --- | --- |
| **分配工作** | 哪个人或 Agent 应当负责这个任务？ |
| **共享信息** | 哪个接收方可以获取哪些信息？ |
| **选择行动** | 流程应当执行、调整，还是申请授权？ |
| **验收结果** | 交付物是否满足验收标准？ |

## 快速开始

需要 Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)。此示例无需 API key，也无需下载模型。

```bash
git clone https://github.com/Juneo-Lab/decision-forge
cd decision-forge
uv sync --group dev

uv run decision-forge validate examples/customer_support/project.yaml
uv run decision-forge build    examples/customer_support/project.yaml --output runs/demo
uv run decision-forge export   runs/demo --output runs/demo-decision --format decision
```

虚构的 `Northstar Support` 示例包含一份约 1.2 KB 的制度手册，可生成完整、经过验证的数据集：

| 产出 | 数值 |
| --- | --- |
| 接受样本 | 336（请求 336，实际 336） |
| 按决策面 | 分配 96 · 共享 96 · 行动 96 · 验收 48 |
| 按切分 | 训练 196 · 开发 84 · 测试 56，按场景族分配 |
| 因没有允许选项而拒绝 | 0 |
| 模型调用 | 0 |

`runs/demo/` 中包含 `episodes.jsonl`、`rejected.jsonl`、`distribution.json`、`dedup.json`、
`report.json`、`report.md`、哈希绑定的 `project.snapshot.json` 和经过完整性校验的 `manifest.json`。
重建复用内容寻址的场景缓存；输入发生变化时会报错，避免与旧运行混合。

## 数据为什么可信

每个发布样本的标签都由规则解释器重新计算。来源检查和审核记录将声明规则与组织文档关联起来。

| 属性 | 实现方式 |
| --- | --- |
| **规则验证的标签** | 确定性解释器逐候选应用优先级、拒绝优先和集合值平局规则。合成提出状态，解释器给出标签。 |
| **有来源依据的规则** | 对照源文验证引文，允许空白规范化。`compile-corpus` 根据抽取的主张重新计算跨度和来源定位符。 |
| **内容绑定的审核** | 编译结果以草稿交付。构建要求所有当前审核项获得接受记录，包括规则、节点、冲突和 `dataset:recipe`。手工编写的规格可以直接使用。 |
| **场景族隔离** | 相关变体共享切分。去重保留留出集副本并报告跨切分重叠；扩展导入验证当前场景、场景族和切分的来源关系，并重新推导所有模型可见字段。 |
| **检查点恢复** | 复用已完成的抽取块和已接受的合成记录。失败或中断调用保留预算预留；响应在检查点保存前丢失时，可能需要重新调用。 |
| **语料编译与合成预算** | `compile-corpus` 按 HTTP 尝试预留预算；`expand` 按逻辑提案调用及输入字节预留，响应格式回退可能在一次提案调用内发出多次 HTTP 请求。两者保留预算日志，并支持显式提高预算后恢复。 |
| **可见的分布缺口** | 报告请求配额、实际配额、被拒提案和剩余缺口。只有通过验证的样本进入数据集。 |

## 训练交接

四种导出格式保留运行中的场景族切分分配。`canonical` 保留集合值标签；其他格式省略含多个有效选项的
样本，因此也可能省略整个场景族。

| 格式 | 形态 | 用途 |
| --- | --- | --- |
| `decision` | 显式 `options`、对齐的 `target_probs`、场景族分组与溯源 | candidate-readout 训练（推荐） |
| `canonical` | 输入/目标对，保留集合值标签 | 保留歧义的自定义管线 |
| `typed` | 带逐候选判据的选择题 | 约束解码与评测框架 |
| `sft` | 带单个答案的聊天消息 | 参考 SFT 基线 |

```bash
uv run decision-forge export runs/demo --output runs/train --format decision
uv run python scripts/finetune_evaluation.py prepare runs/demo --output runs/plan --budget-examples 2000
```

`decision` 格式生成 `objective: candidate_readout` 的记录，权重为 1，并带来源信息。
[`decision_forge.training.encode_decision`](docs/decision-training.md) 将其编码为原生非思考前缀：
使用单 token 候选标签，目标概率随候选顺序一起调整，不追加答案 token，遇到截断则报错。
仅优化普通答案 token SFT 损失的训练器无法直接使用这种编码；契约明确规定了训练目标。

`scripts/finetune_evaluation.py prepare` 写入选定的训练记录、ID、文件摘要及所需训练目标。
`evaluate` 在留出用例上评估显式选定的端点；`compare` 要求数据集、用例顺序和提示词相同。
固定预算训练与决策质量提升的实测属于下一项评估里程碑。

## 编译自己的文档

```bash
export FORGE_API_KEY="your-provider-key"
uv run decision-forge compile-corpus ./private/corpus.yaml --output runs/acme \
  --base-url https://your-model-host.example/v1 --model your-model

uv run decision-forge review-status runs/acme/project.json
uv run decision-forge review-record runs/acme/project.json \
  --item ITEM_ID_FROM_REVIEW_STATUS --decision accepted \
  --reviewer you --reason "Explain the evidence supporting this specific item." \
  --fingerprint FINGERPRINT_FROM_REVIEW_STATUS
# Review every rule, node, conflict, and dataset:recipe item using its returned ID and fingerprint.
# Build after review-status reports ready: true.
uv run decision-forge review-status runs/acme/project.json
uv run decision-forge build runs/acme/project.json --output runs/acme-data
```

按[语料清单格式](docs/organization-compiler.md)创建 `corpus.yaml`。支持带版本和访问元数据的文本、
Markdown、JSON/YAML、PDF、DOCX/XLSX/PPTX、HTML、CSV/TSV 和 OCR 图片输入。安装 `documents`
启用办公文档与 PDF 解析，安装 `ocr-rapid` 启用本地 PP-OCR 小模型。
[文件格式与 OCR](docs/document-ingestion.md)说明自动识别格式、Tesseract 和显式视觉 OCR 服务配置。

<details>
<summary><b>编译过程中发生了什么</b></summary>

<br>

抽取过程分块且可恢复；装配要求规则证据能够关联到已抽取的主张。无效 schema 和歧义引文会产生验证错误；规则冲突进入审核队列裁决。
源文快照和已解析的生成对象保存在本地私有产物中，包括缓存与修复记录。服务凭证不写入这些产物。

可选的[配方规划器](docs/generation.md)把目标分布转成精确的逐单元配额，测量确定性场景池在去重后的供给，
区分可以通过模型补充的工作与需要更多源材料的工作。验证式合成命令 `expand` 只发布标签能够根据
声明规则重放验证的提案。

</details>

## 证据

<details open>
<summary><b>验证、基准测试与真实模型运行</b></summary>

<br>

**本地验证（macOS ARM64、Python 3.12、全部可选依赖）：** 280 项测试、88.41% 语句覆盖率，以及 Ruff、严格 mypy、
严格 MkDocs、wheel 和 sdist 构建。CI 包含 10 项任务：代码质量、6 项测试（Ubuntu 与 macOS ×
Python 3.11/3.12/3.13）、固定版本的 Data Designer 与 LangGraph 适配器契约，以及 Docker 中的 Harbor
oracle 与 no-op 对照验证，加上真实本地 OCR 推理与七格式导入。CodeQL 与 OpenSSF Scorecard 会在拉取请求
和定期任务中扫描仓库。

**本地 OCR：** [七格式导入测试](experiments/2026-11-ingestion/README.md)归档了原生文件解析结果，
以及使用内置 PP-OCR 小模型准确识别 PNG 和扫描 PDF 的结果。Tesseract 与视觉模型服务已有协议测试，
模型质量另行评估。

**离线基准：** 对 25、100、400 个合成场景族测量冷缓存和热缓存构建。[基准报告](docs/benchmarks.md)
记录实测吞吐、主机与依赖版本以及一致的数据集摘要，同时归档原始 JSON。

**真实模型运行（归档于 `experiments/`，使用虚构输入）：**

| 实验 | 模型 | 结果 | 记录成本 |
| --- | --- | --- | --- |
| [文档编译](experiments/2026-10-m1/README.md) | Claude Opus 5.5、GPT-6.1 Sol | 各自 13/13 精确主张、31/32 决策一致 | 成功运行：$0.293428 / $0.1505675 |
| [多步工作流](experiments/2026-10-workflows/README.md) | 同上两个模型 | 各自 3/3 任务、11/11 允许决策 | 合计 $0.036412，22 次调用 |

编译账本还记录了早期诊断与失败尝试：已知成本合计 $1.08587197，另有 5 次尝试成本未知，实际总账单
金额未知。参考用例由维护者编写。归档结果记录了小规模虚构输入上的行为；组织数据训练质量评估另设里程碑。

</details>

## 本版本的支持范围

<table>
<tr>
<th width="50%">已支持</th>
<th width="50%">后续里程碑</th>
</tr>
<tr>
<td valign="top">

- 带版本和访问元数据的文本、Markdown、JSON/YAML、PDF、DOCX/XLSX/PPTX、HTML、CSV/TSV 组织源材料。
- 使用本地 PP-OCR 小模型、Tesseract 或显式视觉模型服务的图片及扫描 PDF OCR。
- 将文档编译为规则、节点、候选和场景，并设置内容绑定的审核门禁。
- 四类决策面的确定性标签推导。
- 分布配额、可行性报告、验证式合成和全局去重。
- 场景族级切分隔离及泄漏记录；可恢复且经过完整性校验的运行。
- 四种训练导出格式，包括 candidate-readout 决策契约。
- 多步模拟业务工作流、独立轨迹评分和 Harbor 任务导出。
- 运行事件接入、经过审核的开发反馈和保持切分的数据集更新。

</td>
<td valign="top">

- 固定预算微调对比与下游决策质量提升实测。
- 更广泛、经过业务审核的组织语料。
- 大语料检索和跨文档规则解析（当前限制：每个源文件 2 MB、每个语料集 32 MB、单次装配默认主张上限 1 MB）。
- 多评测框架模型对比与 Verifiers 适配器。
- PyPI 包发布和 schema 迁移工具。

</td>
</tr>
</table>

合成命令 `expand` 在审核后的规则规格内提出决策时刻的状态。制度抽取与修订通过文档编译和审核完成。
[路线图](ROADMAP.md)将每项剩余验收工作对应到里程碑。

## 文档

| 指南 | 内容 |
| --- | --- |
| [项目状态](docs/status.md) | 已实现的路径与待完成验证 |
| [架构](docs/architecture.md) | 组件边界与数据流 |
| [规格](docs/specification.md) | 规则、场景与配方语义 |
| [文件格式与 OCR](docs/document-ingestion.md) | 格式识别、解析器与 OCR 后端 |
| [组织编译器](docs/organization-compiler.md) | 接入、抽取、审核与恢复 |
| [生成规划](docs/generation.md) | 配额、验证式合成与去重 |
| [决策训练](docs/decision-training.md) | 导出契约与 candidate-readout 编码 |
| [平台工作流](docs/platform-workflows.md) | 多步任务、评分、反馈与更新 |
| [数据协议](docs/data-protocol.md) | 评估协议与报告规则 |
| [基准测试](docs/benchmarks.md) | 离线吞吐协议与参考结果 |
| [发布流程](docs/releasing.md) | 打标签、带证明的产物与校验 |
| [参考](docs/references.md) | 相关工作与集成候选 |

## 开发

```bash
uv sync --group dev --group docs --extra documents
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest --cov=decision_forge --cov-fail-under=80
uv run mkdocs build --strict
uv build
```

运行一次 `uvx pre-commit install`，即可在每次提交前执行相同的格式与配置检查。

## 社区

| 文档 | 用途 |
| --- | --- |
| [参与贡献](CONTRIBUTING.md) | 评审门槛、标签分类与完成标准 |
| [治理](GOVERNANCE.md) | 决策机制 |
| [支持](SUPPORT.md) | 提问渠道 |
| [安全](SECURITY.md) | 漏洞报告与发布产物校验 |
| [行为准则](CODE_OF_CONDUCT.md) | 项目空间中的行为规范 |

代码与技术文档使用英文；README 同时维护[英文原文](README.md)和本中文译本。

## 引用

在发表工作中使用 Decision Forge 时，请注明使用的版本。GitHub 的 **Cite this repository** 按钮读取
[CITATION.cff](CITATION.cff)；导出格式带版本标识，[`CHANGELOG.md`](CHANGELOG.md) 记录全部变更。

```bibtex
@software{decision_forge,
  title   = {Decision Forge: Organizational Decision Data Infrastructure},
  author  = {{Decision Forge contributors}},
  version = {0.3.0},
  license = {Apache-2.0},
  url     = {https://github.com/Juneo-Lab/decision-forge}
}
```

## 许可证

Decision Forge 采用 [Apache-2.0](LICENSE) 许可证，署名信息见 [NOTICE](NOTICE)。
