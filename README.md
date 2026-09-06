# 时序论文追踪

每天检索 arXiv 上的 **Time Series Forecasting** 相关论文，生成可以部署到 GitHub Pages 的中文网页。支持原文摘要展开、日期筛选、标题/摘要/作者搜索、多主题订阅。无需大模型、API 密钥或第三方 Python 依赖。

## 本地运行

需要 Python 3.9 或更新版本：

```sh
python3 paper_tracker.py update
python3 -m http.server 8000 --directory dist
```

打开 http://localhost:8000 。首次检索需要能访问 `export.arxiv.org`。只生成网页、不联网检索：

```sh
python3 paper_tracker.py build
python3 -m unittest discover -s tests -v
```

## 部署到 GitHub Pages

1. 在你的 GitHub 账户下创建公开仓库，默认分支使用 `main`，上传本项目全部文件（包括 `.github` 和 `data`）。不要上传被 `.gitignore` 排除的构建目录。
2. 在 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**。
3. 在 **Settings → Actions → General** 允许工作流运行；仓库或组织策略需允许工作流写入 `main`，用于持久化论文数据。若分支保护禁止机器人推送，需为此专用仓库调整规则。
4. 打开 **Actions → Daily papers → Run workflow**。成功后在 Pages 设置或工作流的部署结果中获取网址：`https://你的用户名.github.io/仓库名/`。

默认北京时间每天 **09:17**（UTC 01:17）执行。时间在 `.github/workflows/daily.yml` 的 cron 中修改。修改配置、网页或检索器并推送到 `main` 也会触发更新。Actions 可能延迟，不保证准点；不要把它当作严格实时调度器。

没有新增论文时仍保存成功时间。抓取失败时发布旧数据与失败提示，并使工作流最终显示失败；请在 GitHub 通知设置中开启工作流失败提醒。网页超过 48 小时没有成功更新会提示检查定时任务。公开仓库长期无活动可能被停用定时任务：到 Actions 启用工作流，再手动运行一次。正常每日状态提交会保持仓库活动，但不能依赖它应对权限或调度故障。

代码和结果公开；不要在配置或论文数据里放密码。此项目无需任何自定义 Secrets。

## 如何增加或修改检索词

以后可以直接请助手修改，也可以在 GitHub 网页上自己操作，无需在电脑上安装软件。**检索词保存在 [config.json](config.json)，修改这份 README 中的示例不会改变实际检索。**

### 当前订阅的方向

| 网页主题 | 检索范围 |
| --- | --- |
| Time Series Forecasting | 时间序列与预测相关论文 |
| 时序基础模型 | 时间序列 + foundation model / TSFM 等表述，或 Chronos、TimesFM、Moirai、Lag-Llama、TinyTimeMixer 等模型名称 |
| 时序基础模型效果增强 | 在“时序基础模型”的条件上，再要求命中下表中至少一个增强方法关键词 |

增强方法关键词按用途分为：

| 方向 | 当前覆盖的关键词 |
| --- | --- |
| 微调与适配 | fine tuning、finetuning、fine tune、fine tuned、adaptation、adapter、adapters、LoRA、PEFT |
| 提示与上下文学习 | prompt、prompting、in context learning |
| 检索与数据增强 | retrieval、augmentation |
| 校准、集成与蒸馏 | calibration、ensemble、ensembling、distillation |
| 推理时适配与外部信息 | test time、covariates、exogenous、residual |

两个新主题不额外强制出现 forecasting，因此也会覆盖基础模型在其他时序任务中的应用。效果增强主题是基础模型主题的子集，重复论文仍只保存一份。关键词只用于发现候选论文，命中不代表论文已经证明效果提升；例如摘要中讨论微调的局限也可能命中。

关键词参考了 [TS-RAG](https://arxiv.org/abs/2503.07649) 的检索增强方向、[FedChronos](https://arxiv.org/abs/2608.01290) 的 LoRA 微调方向，以及 [Google 的上下文微调研究](https://www.research.google/blog/time-series-foundation-models-can-be-few-shot-learners/)。

### 在 GitHub 上修改并保存

1. 登录拥有本仓库写入权限的 GitHub 账号，打开 [config.json 编辑页面](https://github.com/chenghuang7/paper-search/edit/main/config.json)。也可以在仓库首页点击 `config.json`，再点击右上角的铅笔按钮。
2. 按下面的示例修改关键词或增加主题。保留文件中的 `title`、`initial_days`、`overlap_days` 和 `topics` 等其他设置。
3. 点击右上角 **Commit changes…**，填写简短说明，例如“增加异常检测订阅”；选择直接提交到 **main** 分支，再点击 **Commit changes** 确认。
4. 保存后会自动启动检索，不用等到第二天。打开 [Actions](https://github.com/chenghuang7/paper-search/actions)，找到最新的 **Daily papers** 任务；等待显示绿色对勾，再刷新[论文网站](https://chenghuang7.github.io/paper-search/)。`Checks` 是代码检查，网站发布结果要看 `Daily papers`。

若任务显示红叉，点击任务名称，再点击 **update-and-publish**，展开标红的步骤查看原因。配置写错时，回到 `config.json` 修正并保存，会自动再跑一次；若只是临时网络问题，可以在失败任务页面右上角点击 **Re-run jobs → Re-run all jobs**（按钮也可能直接显示 **Re-run all jobs**）。

### 看懂关键词规则

下面是一个简化的**单个主题**，位于 `topics` 数组中，不能直接替换整个配置文件：

```json
{
  "id": "time-series-forecasting",
  "name": "Time Series Forecasting",
  "groups": [
    ["time series", "time-series"],
    ["forecasting", "prediction"]
  ],
  "exclude": []
}
```

- `id`：主题的唯一标识，只使用小写字母、数字和连字符。
- `name`：网页里显示的主题名称。只修改它不会改变检索范围。
- `groups`：**同一组是“或”，不同组是“且”**。上述示例要求标题与摘要合并后，既包含 `time series` 或 `time-series`，又包含 `forecasting` 或 `prediction`。
- `exclude`：排除词。任意一个词命中标题或摘要，就不计入这个主题；`[]` 表示不排除。

常见修改方式：

| 想做什么 | 怎么改 | 效果 |
| --- | --- | --- |
| 补充预测的其他说法 | 在预测词所在的方括号中追加 `"predictive"` | 与原来的预测词任选其一，扩大覆盖 |
| 只关注包含大模型术语的预测论文 | 在 `groups` 中追加一组 `["foundation model", "foundation models", "LLM"]` | 新增一个必须满足的条件，收窄覆盖 |
| 排除交通方向 | 将 `exclude` 改成 `["traffic"]` | 标题或摘要提到 traffic 的论文会被排除 |
| 同时关注预测和异常检测，分别浏览 | 在 `topics` 中新增一个主题，参考下一节 | 网站上可以按两个主题分别筛选 |

当前实际配置包含比简化示例更多的预测词形；补充词语时保留已有词即可。匹配大小写不敏感，常见连字符和空白会归一化。词语使用普通文本，不要填写 `AND`、`OR` 等查询语法，也不要在词语里加入引号、反斜线或换行。

### 示例：新增“时间序列异常检测”主题

在 `topics` 中追加主题对象，与原主题之间用英文逗号隔开；已有主题继续保留。下面是一份**可完整替换 `config.json`** 的配置，保留默认预测词并增加异常检测：

```json
{
  "title": "时序论文追踪",
  "initial_days": 30,
  "overlap_days": 7,
  "topics": [
    {
      "id": "time-series-forecasting",
      "name": "Time Series Forecasting",
      "groups": [
        ["time series", "time-series"],
        ["forecast", "forecasts", "forecasting", "forecasted", "forecaster", "forecasters", "prediction", "predictions", "predictive", "predicting"]
      ],
      "exclude": []
    },
    {
      "id": "time-series-anomaly-detection",
      "name": "Time Series Anomaly Detection",
      "groups": [
        ["time series", "time-series"],
        ["anomaly detection", "outlier detection"]
      ],
      "exclude": []
    }
  ]
}
```

JSON 使用英文双引号、逗号和方括号，最后一项后面不要加逗号，不支持注释。若已添加其他自定义主题，不要直接用上面的完整示例覆盖，应只追加新的主题对象。

### 修改后会发生什么

- 修改订阅后默认至少补查最近 **30 天**；如有更早的未完成检索窗口，也会继续补抓。
- 网站会重新计算已有论文匹配的主题；同一篇论文命中多个主题时只保存一份。
- 删除主题时，从 `topics` 中移除对应对象，并保留至少一个主题。已保存的论文不会从数据文件中删除，但不再匹配任何当前主题的论文会从网页隐藏。
- 如果只想调整网页上的临时搜索或日期筛选，直接在论文网站操作即可；这不会改变每天自动执行的订阅规则。

关键词匹配可能收录只是提到预测的论文，也可能遗漏使用不同术语的论文，不等于语义相关性判断。

## 数据与恢复

- `data/papers.json` 保存论文、arXiv ID、首次提交/修订/发现时间、主题、上次成功时间和运行状态。
- 首次收录最近 30 天提交的论文；常规运行从上次成功时间前 7 天开始补抓。停跑期间的窗口不会随着今天前移而丢掉。
- arXiv API 只公开支持 `submittedDate` 日期过滤；为捕获旧论文修订，本项目按 `lastUpdatedDate` 倒序分页，在本地到达窗口起点后停止。每次请求至少间隔 3.1 秒，网络失败最多尝试 3 次。
- ID 去掉 `v1`、`v2` 等版本后去重，修订替换原记录，不算新增。初次遇到远早于窗口的旧论文修订不会作为新论文收录。超过 7 天的异常索引延迟可能漏检，可暂时扩大 `overlap_days` 后手动运行。
- 任何主题或分页失败都不提交部分论文结果，也不前移成功时间；仅记录失败状态，下一次从原检查点补抓。结果集在分页期间变化、分页异常或到达 API 上限时也会明确失败。
- 修改订阅会至少重新检索最近 30 天，并重新计算已有论文主题；不再匹配当前主题的旧记录保留在数据文件中，但网页隐藏。
- 网页按首次提交时间倒序、北京时间显示。新增标签表示最近一次成功检索首次收录，初次回填也会标为新增；它不等于当天刚发表。

将来迁移服务器时，同一条 `python3 paper_tracker.py update` 命令可由 cron 调度，静态服务托管 `dist/`，持久化 `data/` 即可。

## 来源与实现说明

原计划以 [BernieZhu/arxiv-daily-digest](https://github.com/BernieZhu/arxiv-daily-digest) 为改造起点。经 GitHub API 读取并审查 `arxiv_digest.py`（文件 blob SHA：`0bee731ad68a994b03cf0cb7a696ed868b195aeb`），发现其 `direct` 模式仍在主流程创建 DeepSeek 客户端，检索则依赖分类的 pastweek HTML 列表，无法直接满足无模型、30 天回填及任意中断补抓要求。

因此保留其关键词配置与每日自动化的产品思路，检索、持久化和网页独立实现。**本项目不是该项目的 fork，未复制其代码。** 上游许可声明归档在 `third_party/arxiv-daily-digest.LICENSE`，仅用于注明参考来源；本项目采用 MIT 许可。

[DailyArXiv](https://github.com/zezhishao/DailyArXiv) 的 Time Series 栏目也是产品参考，未复制其代码。

协议依据：[arXiv API 手册](https://info.arxiv.org/help/api/user-manual.html)、[GitHub Pages 文档](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)、[GitHub 定时工作流说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)。仅覆盖 arXiv，不保证覆盖所有会议、期刊；不包含推送、收藏同步、AI 总结或全文下载。
