# Codex 纠正反馈与协作准则

从用户对 AI 理解、判断或执行方式的纠正中，维护精简、脱敏、可回退的协作准则。通过本地 Codex 生命周期钩子载入当前准则，并在需要时调用技能复盘。

## 工作方式

1. `UserPromptSubmit` 读取当前准则，并登记疑似纠正的匿名摘要。
2. AI 先完成当前任务，再结合上下文判断反馈属于长期偏好、场景要求、临时要求或需求变化。
3. `Stop` 对遗漏反馈补充最多一轮复盘；用户要求暂停时不继续。
4. 更新优先强化、合并或修正已有准则，保留适用边界、版本和回退记录。
5. 下一次输入重新载入当前生效准则。

候选检测依赖关键词，可能遗漏隐含纠正。模型负责语义判断和例子的脱敏改写。运行次数和版本增长不能证明 AI 已减少犯错，实际效果需要在后续相似任务中验证。

## 仓库内容

| 内容 | 用途 |
| --- | --- |
| `learn-from-corrections/SKILL.md` | 技能的触发范围和复盘方式 |
| `learn-from-corrections/scripts/feedback.py` | 钩子、存储、规则更新、版本及回退 |
| `learn-from-corrections/references/seed-rules.json` | 脱敏后的生效准则快照，供首次安装初始化 |
| `learn-from-corrections/references/regression-cases.json` | 通用正反案例 |
| `learn-from-corrections/references/update-protocol.md` | 更新格式与适用边界 |
| `install.py` | 安装技能并合并钩子配置 |
| `export_rules.py` | 从本机规则库导出不含运行标识的准则快照 |
| `test_feedback.py` | 隔离测试，包括去重、并发、暂停、回退与敏感信息拒绝 |

仓库不包含原始对话、运行数据库、个人配置、认证文件、安装备份和本机调试记录。规则快照不包含会话哈希或个人反馈标识。

## 安装

需要 Python 3.9 或更新版本，以及支持本地生命周期钩子的 Codex。运行时使用 Python 标准库，面向 macOS 和 Linux。

```sh
python3 install.py --check
python3 install.py
```

安装会保留已有钩子，并备份所修改的配置和原有同名技能。在新启动的 Codex 中打开 `/hooks`，审阅并信任 `UserPromptSubmit` 和 `Stop`。安装成功、配置被识别、钩子已信任和真实对话已触发是不同状态。

首次安装使用仓库中的准则快照。已存在的本地准则库不会被安装器覆盖。数据默认保存在 `~/.local/share/codex-feedback`；可用 `CODEX_FEEDBACK_HOME` 指定隔离目录。

## 查看与控制

```sh
python3 ~/.codex/skills/learn-from-corrections/scripts/feedback.py status
python3 ~/.codex/skills/learn-from-corrections/scripts/feedback.py pause
python3 ~/.codex/skills/learn-from-corrections/scripts/feedback.py resume
python3 ~/.codex/skills/learn-from-corrections/scripts/feedback.py rollback
```

本方案随对话触发，空闲时不持续调用模型。它不会自动对外发布内容、改变业务仓库或扩大已有授权。

## 测试与更新快照

```sh
python3 test_feedback.py
python3 export_rules.py
git diff -- learn-from-corrections/references/seed-rules.json
```

`export_rules.py` 只导出生效准则的正文与适用范围等必要字段，并移除运行标识；它不导出个人纠正案例或原始对话。文本本身仍应保持通用和脱敏。日常学习在本机发生，本仓库没有配置自动向 GitHub 推送运行数据。

修改生效规则前需检查相关正反例。语义案例演练不能替代真实模型效果对照。

## 接口依据

钩子配置与信任方式参见 [Codex 官方 Hooks 文档](https://learn.chatgpt.com/docs/hooks)。该项目调整提供给模型的上下文和复盘流程，不修改模型参数。
