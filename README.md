# GISAID Flu Download

基于 Selenium 和 Microsoft Edge 自动操作 GISAID EpiFlu，按病毒类型、宿主、亚型或谱系及采样日期筛选记录，分批下载 Metadata、DNA 和 Protein，并合并多个日期批次。使用本项目时必须遵守 GISAID 的访问和数据使用条款。

## 项目结构

```text
.
├── configs/
│   ├── example.yaml          # 可提交的脱敏配置模板
│   └── local/                # 本地配置，Git 忽略
├── skills/
│   └── gisaid-sequence-download/ # 项目级序列下载技能
├── src/gisaid_flu_download/
│   ├── downloader.py         # EpiFlu 下载流程
│   ├── merger.py             # Metadata/FASTA 合并工具
│   └── pipeline.py           # 下载后自动合并的流水线
├── tests/                    # 自动化回归测试
├── pyproject.toml            # 依赖、打包和命令入口
└── AGENTS.md                 # 贡献指南
```

运行数据默认建议放在 `data/`，该目录不会进入 Git。

## 安装

需要 Conda、Microsoft Edge 和具有 EpiFlu 权限的 GISAID 账号。首次使用时创建环境并安装项目：

```bash
conda create -n gisaid_flu_download python=3.10 pip -y
conda activate gisaid_flu_download
python -m pip install -e '.[dev]'
```

Selenium 通常会自动管理兼容的 EdgeDriver；浏览器本身需要提前安装。

## 配置

复制脱敏模板并只修改本地副本：

```bash
cp configs/example.yaml configs/local/H1N1.yaml
```

编辑该文件，填写账号并确认 `runtime.download_root`。真实账号配置只保存在 `configs/local/`，不要提交。

配置中的关键部分：

- `credentials`：GISAID 用户名和密码，禁止提交。
- `runtime.download_root`：本次任务的输出目录；相对路径以启动命令所在目录为基准。
- `filters`：病毒类型、H/N 亚型、B 型谱系、宿主、提交实验室和片段。
- `dates.collection_date`：待下载的完整日期范围。
- `dates.date_ranges`：非空时直接采用这些区间；为空时按 `max_strains_per_range` 自动拆分，成功后原子写回当前 YAML。
- `options`：控制 Metadata、DNA、Protein 和人工验证。
- `runtime.step_retries` 与 `runtime.retry_delay_sec`：控制 Selenium 步骤和自动日期拆分的重试次数与间隔。

首次运行或需要验证码时，建议设置 `headless: false` 和 `require_manual_validation: true`。

## 一条龙下载与合并

推荐使用 `gisaid-run`。每次调用只处理一个 YAML：它会读取配置、完成下载，并根据该 YAML 的 `options.download_*` 设置自动合并对应类型。

```bash
gisaid-run configs/local/H1N1.yaml
```

同一配置可以安全重跑。程序按日期区间检查启用类型的目标文件：非空文件视为已完成并跳过，缺失或空文件会重新下载。因此中断后直接再次执行同一命令即可续传；不要在任务运行期间并发使用同一输出目录。

也可以通过 `python -m gisaid_flu_download <配置文件>` 运行同一流水线。需要运行其他亚型时，分别再次调用；需要单独重跑某一步时，使用：

```bash
gisaid-download configs/local/H1N1.yaml
gisaid-merge ./data H1N1 --types meta dna protein
```

下载目录结构固定为：

```text
<download_root>/
├── meta/<start>-<end>.xls
├── DNA/<start>-<end>.fasta
├── protein/<start>-<end>.fasta
└── gisaid_run.log
```

合并结果写入输入根目录，文件名为 `<name>_meta.xlsx`、`<name>_DNA_merged.fasta` 和 `<name>_protein_merged.fasta`。旧版本若已生成小写 `dna/`，请在合并前一次性重命名为 `DNA/`。

## 测试

```bash
python -m pytest -q
python -m unittest discover -s tests -v  # 无 pytest 时的标准库入口
python -m compileall -q src tests
```

当前测试覆盖 `DNA/` 目录约定、FASTA 的稳定排序与换行、配置加载，以及“先下载、后合并”和失败短路的流水线行为。

## 安全与排错

不要提交账号密码、下载数据、浏览器临时文件或日志。如果凭据曾被提交或共享，应立即轮换。浏览器无法启动时检查 Edge 与驱动版本；验证码无法完成时关闭无头模式；合并器找不到数据时检查输入根目录、文件扩展名及 `meta/`、`DNA/`、`protein/` 的精确大小写。
