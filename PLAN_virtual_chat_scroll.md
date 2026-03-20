# 方案 2：虚拟滚动 Chat Area

## 问题
当前 `SessionScreen` 的 `#chat-area`（`ScrollableContainer`）为每条消息 mount 一个 widget。
长 session（如 70 分钟 Swarm 运行）会积累 200+ widget，Textual 每帧都要对所有 widget 做 layout 计算，导致输入响应严重卡顿。

## 目标
无论消息总量多少，渲染开销恒定。只渲染视口内可见的消息。

## 设计

### 核心思路
用数据驱动替代 widget 驱动：
- 消息存储在内存 list（`state.agent_messages` 已存在）
- chat area 只维护视口内的 widget pool（固定数量，如 30 个）
- 滚动时复用 widget，更新内容

### 组件拆分

#### 1. `VirtualChatArea`（新组件，替代 `ScrollableContainer`）
- 继承 `Widget`，自行管理滚动偏移
- 内部维护固定数量的 slot widget（`_pool: List[MessageSlot]`）
- 属性：
  - `_messages: List[ChatMessage]` — 完整消息列表的引用
  - `_scroll_offset: int` — 当前视口顶部的消息 index
  - `_viewport_size: int` — 视口能容纳的消息数（动态计算）
  - `_auto_scroll: bool` — 是否自动滚到底部（新消息时）

#### 2. `MessageSlot`（轻量复用 widget）
- 单个 slot，可以被赋予任意 `ChatMessage` 来渲染
- 方法：`bind(msg: ChatMessage)` — 更新显示内容
- 内部根据 `msg.role` 切换样式（user/assistant/tool/error）
- 用 `markup=False` 的 `Static` 显示内容，避免 MarkupError

#### 3. 滚动逻辑
- 监听 `ScrollUp` / `ScrollDown` / `MouseScrollUp` / `MouseScrollDown` 事件
- 更新 `_scroll_offset`，重新 bind 可见 slot
- 新消息追加时：若 `_auto_scroll=True`，offset 跳到底部

#### 4. 流式消息处理
- 流式 assistant 消息：最后一个 slot 持续 update，不创建新 widget
- 流式结束后：将最终内容存入 messages list，slot 内容不变

### 文件变更

| 文件 | 变更 |
|------|------|
| `src/tui/components/virtual_chat.py` | **新建** — `VirtualChatArea` + `MessageSlot` |
| `src/tui/screens/session.py` | 替换 `ScrollableContainer` 为 `VirtualChatArea`，重写 `_add_or_update_message` |
| `src/tui/components/message.py` | `ChatMessage` 保留，widget 类不再被 session 直接使用 |

### 性能预期
- 当前：192 widget → 每帧 layout O(N)，N=消息数
- 改后：30 slot → 每帧 layout O(1)，恒定开销
- 内存：消息数据 list 占用远小于 widget 树

### 风险 & 注意事项
- Textual 没有内建虚拟列表，需要自己管理滚动和 widget 复用
- 消息高度不等（tool 消息 1 行，assistant 消息可能很长），viewport_size 需要动态计算
- Markdown 渲染（`finish_streaming` 里的 Static→Markdown 切换）需要在 slot 层面处理
- 需要保证 `scroll_end` 在新消息时仍然流畅

### 里程碑
1. [ ] 实现 `MessageSlot` — 能根据 ChatMessage 渲染不同角色
2. [ ] 实现 `VirtualChatArea` — 固定 pool + 滚动偏移
3. [ ] 集成到 `SessionScreen` — 替换 ScrollableContainer
4. [ ] 处理流式消息 — slot 实时 update
5. [ ] 测试长 session — 验证 500+ 消息下输入响应正常
