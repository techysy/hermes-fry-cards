# 稳定性与维护性优化方案

## 1. 背景

当前项目已经具备较完整的功能闭环，核心能力集中在：
- AST 注入 Hermes gateway hook
- 流式卡片生命周期管理
- Feishu/CardKit 动态更新
- 终态 cleanup 与 fallback 处理

从整体结构来看，项目已进入“稳定性优化阶段”，而不是“功能扩展阶段”。目前最值得优先解决的点，不在于增加新功能，而在于收敛状态管理、降低重入风险、统一失败处理路径。

重点文件包括：
- hermes_fry_cards/controller.py
- hermes_fry_cards/streaming/controller.py
- hermes_fry_cards/streaming/session.py
- hermes_fry_cards/streaming/flush.py
- hermes_fry_cards/patcher.py
- tests/test_controller.py
- tests/test_flush.py

---

## 2. 当前主要问题

### 2.1 Session 生命周期分散，cleanup 逻辑重复

当前项目中，session 管理分散在多个索引和状态入口中：
- _sessions
- _session_keys
- _interrupt_map
- session.state
- session.anchor_id

这些索引在 interrupt、abort、complete、cleanup 等路径中会同时被写入和删除，容易出现：
- stale session 未清理
- 旧 session 和新 session 绑定到同一 key
- interrupt redirect 后残留旧索引
- cleanup 重复执行时触发异常或状态不一致

这是最容易导致“看起来没报错，但卡片状态错乱”的核心问题。

### 2.2 FlushController 状态过多，重入风险高

在 hermes_fry_cards/streaming/flush.py 中，flush 涉及：
- throttle
- pending timer
- in progress
- needs reflush
- last update time
- completed
- card ready

这些状态耦合在一套调度器内，出现复杂重入场景时，容易出现：
- 重复 flush
- timer 未及时取消
- flush 期间继续 schedule 新任务
- 长时间空闲后重刷逻辑不稳定

这类问题在高频 answer / thinking / tool delta 的场景中最明显。

### 2.3 Card creation 和 fallback 逻辑分散

在 hermes_fry_cards/streaming/controller.py 中，card 创建、reply、retry、fallback、session fail、text fallback 多个行为揉在一起。导致的问题是：
- 失败原因难以定位
- 不同分支处理语义不一致
- 业务失败和 API 失败混在一起
- 调试时难以确认是“创建失败”还是“发送失败”

### 2.4 日志信息不足，无法做结构化诊断

项目已经有较多日志，但大多是文本型记录，缺乏标准化事件字段，难以做：
- 成功率统计
- fallback 比例统计
- interrupt 路径统计
- timeout / stale / abort 的原因聚合

这会直接放大问题排查成本。

---

## 3. 优化目标

本方案的目标不是大改功能，而是把以下关键能力做成稳定的工程约束：

1. session lifecycle 统一且幂等
2. flush scheduling 可控且稳定
3. card creation / retry / fallback 统一且可追踪
4. 日志具备结构化语义，便于线上排查
5. 回归测试覆盖关键状态链路

---

## 4. 方案设计

### 4.1 统一 session registry 与 cleanup

目标：
- 让所有 session 的索引维护都走统一入口
- cleanup 必须幂等
- interrupt/abort/complete 都遵循同一语义

具体方案：
- 在 hermes_fry_cards/controller.py 中新增统一方法：
  - register_session
  - dispose_session
  - resolve_session_by_message_or_anchor
  - archive_session_state
- 所有对 _sessions、_session_keys、_interrupt_map 的操作收口
- 对 terminal 状态统一做 cleanup
- 对 interrupt redirect 做统一的 old → new mapping 管理
- 每次 cleanup 前统一检查对象实例是否仍为当前引用，防止重复删除导致 KeyError

目标效果：
- 一条消息只允许一个 active session
- cleanup 可以被重复调用，但不产生副作用
- 旧索引不会残留

验收标准：
- 同一 message_id 不会出现两个 active sessions
- cleanup 可重复执行但不报错
- interrupt redirect 后旧映射全部清理完成

---

### 4.2 收敛 FlushController 状态机

目标：
- 让 flush 的调度逻辑更清晰
- 降低重入和重复任务的概率
- 让长时间空闲后的重刷行为更稳定

具体方案：
- 重构 hermes_fry_cards/streaming/flush.py 中状态定义
- 明确区分以下状态：
  - ready / not ready
  - completed / active
  - pending timer
  - in progress
  - needs reflush
- 把“是否需要重刷”和“是否有定时器”拆分管理
- 将 schedule_update 和 _do_flush 的调用关系减少一层耦合
- 对长时间空闲后的 flush 机制单独设计，不再和正常节流逻辑混在一起

目标效果：
- 高频 delta 不会导致 flush 堆积
- flush 期间的重复调度被降到最低
- timer 与 in-progress 不会同时残留

验收标准：
- wait_for_flush 行为稳定
- mark_completed 后不再接受新 flush
- 高频更新时不会出现重刷风暴

---

### 4.3 抽离 Card creation / retry / fallback 的统一策略

目标：
- 把创建卡片与 fallback 的决策逻辑从业务代码中剥离出来
- 让失败路径可追踪、可复用、可测试

具体方案：
- 在 hermes_fry_cards/streaming/controller.py 中拆分：
  - create_card
  - reply_card
  - retry_card_creation
  - fallback_to_chat_message
  - handle_card_failure
- 区分两类失败：
  - API 失败
  - 业务失败
- 对创建失败、reply 失败、fallback 失败分别写清晰状态
- 让最终策略只由统一方法决定，不要散落在多个分支中

目标效果：
- 失败原因更容易定位
- fallback 方案行为一致
- 调试和维护成本下降

验收标准：
- 创建失败不会吞掉错误上下文
- fallback 路径严格遵循统一策略
- 失败分类清晰，可被日志或诊断工具识别

---

### 4.4 增加结构化日志与失败分类

目标：
- 提升诊断效率
- 让线上问题更容易定位

具体方案：
- 在关键状态点增加统一日志字段：
  - event
  - message_id
  - session_key
  - chat_id
  - state
  - reason
  - fallback_applied
- 分类日志事件：
  - session_created
  - session_disposed
  - card_created
  - card_reply_failed
  - fallback_to_text
  - interrupt_redirect
  - stale_pruned
  - flush_skipped
  - cleanup_idempotent
- 对 timeout、API error、fallback、abort 统一分类

目标效果：
- 执行一条消息链路时，可以快速判断哪一步失败
- 线上错误排查效率明显提升

验收标准：
- 日志能复现关键状态流转
- 失败原因清楚，不再只有 generic failed

---

### 4.5 测试回归补齐

目标：
- 用测试锁定稳定性改动
- 防止修复一个问题时放大另一个问题

建议新增与补齐的测试：
1. interrupt 后旧 session 被正确清理
2. cleanup 可重复执行且幂等
3. flush 在高频更新时不重复堆积
4. card creation timeout 正确走 fallback
5. abort / complete / interrupted 路径不残留旧 key
6. 重复 message_id 只维持一个 active session

重点测试文件：
- tests/test_controller.py
- tests/test_flush.py

验收标准：
- 新增关键回归测试全部通过
- 相关行为无回归

---

## 5. 实施优先级

### P0：必须优先
1. session registry 与 cleanup 统一
2. flush state machine 收敛
3. card creation / fallback 统一策略

### P1：建议立刻跟进
4. 结构化日志补齐
5. 回归测试补齐

### P2：后续增强
6. patcher 兼容性校验强化
7. 更细粒度的性能和状态诊断
8. 针对不同 Hermes 版本的兼容性亮灯机制

---

## 6. 实施顺序建议

推荐顺序如下：
1. 先修正 session lifecycle
2. 再修正 flush scheduling
3. 再抽离 card fallback 逻辑
4. 最后补日志和测试

这样改动最容易控制，不容易制造大范围兼容性问题。因为这类系统最容易在状态重构时出现“看似正常但真实链路错乱”的情况。

---

## 7. 交接给其他 agent 的直接说明

下面这段可以直接复制给下一位 agent：

> 目标：在 Hermes Fry Cards 中做一次稳定性与维护性优化，优先修复 session lifecycle、flush scheduling 和 card creation fallback 的问题。
>
> 核心文件：
> - hermes_fry_cards/controller.py
> - hermes_fry_cards/streaming/session.py
> - hermes_fry_cards/streaming/flush.py
> - hermes_fry_cards/streaming/controller.py
>
> 实现要求：
> 1. 统一 session registry 和 cleanup，确保幂等
> 2. 收敛 FlushController 状态，避免重入和重复 flush
> 3. 抽离 card creation / retry / fallback 的统一逻辑
> 4. 增加结构化日志和失败分类
> 5. 补齐对应回归测试
>
> 验收标准：
> - interrupt / abort / cleanup 不会残留 stale state
> - flush 不会重复堆积或重入
> - card creation 失败可正常 fallback
> - 相关 tests 全部通过

---

## 8. 总结

这次优化的核心思想不是“加更多功能”，而是把项目从“能跑”推进到“稳定、可维护、可诊断”的阶段。
在这种类型的流式卡片中，真正决定项目长期质量的，不是功能数量，而是状态管理、失败处理和调度稳定性。

如果执行顺序正确，这些改动不会大规模改结构，但会明显降低线上不稳定性和排查成本。
