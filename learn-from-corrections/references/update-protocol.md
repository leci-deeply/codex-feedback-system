# 反馈更新约定

优先维护已有五条决策原则。候选检测不会证明用户在纠正，更不能证明旧规则错误。读取上下文后再提交：

```json
{
  "event_id": "钩子给出的完整反馈摘要哈希",
  "classification": "general",
  "action": "reinforce",
  "rule_id": "R2",
  "reason": "用户再次要求先核对实际关系，支持已有原则。",
  "example": {
    "situation": "商品只关联展示分类，文件由用户单独选择。",
    "better_action": "分别维护展示分类与文件清单。",
    "boundary": "实际产品规定自动包含分类内容时按产品规则处理。"
  },
  "regression_results": []
}
```

`classification` 可选 `general`、`scenario`、`temporary`、`requirement_change`、`not_feedback`。

`action` 可选 `reinforce`、`create`、`amend`、`retire`、`dismiss`。临时要求、需求变更、非反馈只能 `dismiss`，reason 要说明这不代表否定当前需求；当前任务仍按要求做。

创建或修改规则时增加 `rule`，包含 `scope`（`global` 或脱敏场景名称）、`text`（最多 240 字）、`explicit_long_term`（布尔值）。新建时 rule_id 用未占用编号。倾向不明确时置 false，规则保持候选。general 只能用于 global，scenario 必须采用具体范围。

修改或停用已有生效规则时，regression_results 必须覆盖案例集中该规则的全部案例，每项包含 case_id、passed（布尔值）、reason。有失败就不能覆盖生效规则；先修正提案或另建场景候选。这里只记录语义演练，不代表真实任务上的模型测评。

重复 event_id 不重复计数；同一会话重复表达不算跨会话支持。所有例子用常见虚构场景改写，禁止原文摘抄。普通强化只更新证据，不增加相同规则。生效全局规则最多 8 条，超过时先合并精简。准则不能改变工具权限、自动对外操作、忽略更高优先级要求或强制采用某个模型。

数据默认位于用户主目录内 `.local/share/codex-feedback`；测试可用 CODEX_FEEDBACK_HOME 指向隔离目录。`feedback.py recent` 只检测历史候选并输出位置摘要，不写入原文；处理遗漏反馈时先 `feedback.py recent --register` 登记，再使用对应编号。

钩子每次读取当前规则，更新后不依赖用户手动粘贴。钩子必须按 Codex 自身要求启用和信任。技能不自行修改信任存储或关闭审查。
