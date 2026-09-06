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

## 修改订阅

编辑 `config.json`。每个主题的 `groups` 是“组间 AND、组内 OR”：默认要求标题与摘要合并后，既出现时间序列表述，也出现预测表述。大小写不敏感，连字符和空白归一化；`exclude` 中任一词命中就排除。

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

在 `topics` 中追加同结构对象即可添加订阅；`id` 使用唯一小写字母、数字或连字符。关键词是普通文本，不能包含引号、反斜线或换行；不要直接填写 arXiv 查询语法。默认配置包含更多预测词形。匹配基于文字，可能把只是提到预测的论文收入，也可能遗漏完全不同的术语，不等于语义相关性判断。

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
