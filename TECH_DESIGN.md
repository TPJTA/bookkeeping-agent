# 记账 Agent 技术设计文档

> 通过飞书发送订单截图 → 多模态大模型识别交易信息 → 写入飞书多维表格 → 用户确认/修改。
> 本文档用于动手写代码前的方案对齐,会随讨论持续更新。

最后更新:2026-05-25(v5 — Apple 快捷方式 Webhook 入口)

---

## 1. 目标与范围

**做什么:** 用户在飞书里给机器人发一张支付/订单截图,或通过 Apple 快捷方式把截图 POST 到 Webhook;程序自动识别出交易信息(商户、商品、类别、金额),先以「待确认」状态写入飞书多维表格,并在飞书里给出交互卡片;用户点「确认」→ 记录变「已确认」;用户直接**回复消息**(自然语言)→ 程序理解其意图、修改对应字段,再次发卡片请确认。

**本期范围(MVP)— 已全部实现 ✅:**
- 单张图片 → 单笔交易识别
- 写入飞书多维表格(Bitable),含截图附件
- 交互卡片「确认」按钮 → 状态置「已确认」
- 用户文本回复 → LLM 理解修改意图 → 旧卡片置「已失效」,挂出新卡片
- 开发期用长连接(WebSocket)接收事件,无需公网
- **Apple 快捷方式 Webhook 入口**:`POST /webhook/order-screenshot`,成功返回只表示已接收;后续识别/确认都在飞书确认群进行
- **多入口结构**:飞书与 Web 都是 channel,核心业务与渠道解耦
- **实施中追加(见 §11)**:LLM 流式输出 + 打字机卡片、handler 异步化、message_id 幂等、图片缩放

**暂不做(后续迭代):**
- 多张图片批量、一张图多笔交易
- 删除记录、报表统计、月度汇总
- 已确认记录的再修改(本期只有「待确认」状态可被文本修改)

---

## 2. 已确定的关键决策

| # | 决策点 | 选择 | 说明 |
|---|--------|------|------|
| 1 | LLM 接入方式 | **OpenAI 兼容网关**(`openai` SDK + `MODAL_APP_BASE`) | LangGraph 与 SDK 无关。当前 `.env` 只有 `MODAL_*`,走 OpenAI 兼容网关最自然。模型 = `GLM-4.6V`(多模态,支持图片输入) |
| 2 | 记录载体 | **飞书多维表格 Bitable** | 结构化、天然适合统计筛选;`app_token` 与 `table_id` 走环境变量 |
| 3 | 事件接收 | **长连接 WebSocket**(`lark.ws.Client`) | 开发期免公网、免内网穿透 |
| 4 | 候选记录决策 | **交互卡片按钮** | 待确认卡片带「✅ 确认」和「撤销」按钮,value 携带 `record_id` |
| 5 | 修改机制 | **用户回复消息 → LLM 理解修改意图** | 不做独立"修改按钮";直接回复自然语言,程序合并修改 |
| 6 | 截图存档 | **作为附件存入 Bitable** | 便于事后核对原图 |
| 7 | 架构原则 | **核心业务与渠道解耦** | 为未来更多入口预留(见 §3、§8) |
| 8 | 输出规范 | **解析 + Pydantic 校验 + 失败重试(repair loop)** | `GLM-4.6V` 经网关不支持 `response_format`,改由图里 `validate` 节点把关、失败回喂错误重试 |
| 9 | 修改的关联 | **靠回复的 `parent_id` → 映射出 `record_id`** | 不必拉父消息内容;字段值从 Bitable 读取(事实来源) |
| 10 | 修改后流程 | **改完仍「待确认」,重发卡片再请确认** | 已确认记录被回复时,提示去多维表格自行修改 |
| 11 | Webhook 返回语义 | **HTTP 202 = 已接收** | 不等待识别/写表;成功、失败、非交易截图都反馈到飞书确认群 |
| 12 | 来源标记 | **Bitable `来源` 单选** | 新记录写 `飞书` 或 `快捷方式`;历史记录不回填 |
| 13 | Web 卡片截图 | **仅快捷方式来源展示缩略图** | 飞书来源已有原始图片消息,不重复展示 |
| 14 | Webhook 目标会话 | **请求表单携带 `WEB_REVIEW_CHAT_ID`** | 不再校验 Bearer token;按请求参数决定反馈到哪个飞书会话 |

---

## 3. 整体架构

### 3.1 分层原则(关键)

为支持「未来不止飞书一个入口」,采用 **渠道适配层 / 业务核心层 / 基础设施层** 三层:

- **channels(渠道适配层)**:每个入口一个子包(`feishu/`,未来可能 `wecom/`、`web/` 等)。负责该渠道特有的 I/O:接收事件、下载图片、把渠道事件**翻译成核心层的标准输入**、以及把核心层产出的"回复内容"用该渠道的方式**发出去**。
- **core(业务核心层)**:**完全不认识飞书**。输入是「图片字节 + 元信息」,输出是「识别结果 + 要回复什么(文本/卡片数据)」。LangGraph 图就在这一层。
- **基础设施层**:`llm/`(GLM 调用)、`storage/`(Bitable 读写)。被 core 调用,本身与渠道无关。

> 一句话原则:**core 只决定"要回复什么内容",channel 决定"用什么形式、往哪儿发"。** 这样换/加入口时,core 一行不用动。

### 3.2 数据流

```
                    飞书 (Lark)
                        │  事件:消息 / 卡片按钮回调
                        │  (长连接 WebSocket 推送)
                        ▼
         ┌────────────────────────────────┐
         │ channels/feishu/app.py          │  长连接 + 事件分发 + 后台线程
         │ channels/feishu/client.py       │  SDK 封装(下载/发卡/patch)
         │ channels/feishu/cards.py        │  交互卡片 JSON 构造器
         └───────────────┬────────────────┘
                         │
        ┌────────────────┼─────────────────┐
        │                │                 │
   图片消息          卡片「确认」按钮      文本回复(修改)
        │                │                 │
        ▼                ▼                 ▼
 core: Flow A      core: confirm()    core: Flow A 的修改分支
 (LangGraph)                          /modify()
        │                │                 │
        ▼                ▼                 ▼
  下载图→GLM识别→    查记录→状态改      载入原记录→LLM合并修改→
  写Bitable待确认    「已确认」→        更新字段(仍待确认)→
  +传附件→产出卡片   产出"已记账"回复    产出新卡片再请确认
        │                │                 │
        └────────────────┴─────────────────┘
                         ▼
              channel 把"回复内容"发回飞书
```

> 两阶段(待确认→已确认)用 Bitable 里那一行表格本身当持久化状态,流程之间通过 `record_id` 串联,无需让 LangGraph 实例长时间挂起等用户。
> 进阶备选(本期不用):LangGraph `interrupt` + checkpointer 的 human-in-the-loop,对"长时间等待/进程重启"场景偏重,故不采用。

---

## 4. Flow A:截图识别记账(核心 LangGraph)

### 4.1 State 定义

```python
from typing import Any, Callable, Optional, TypedDict

class BookkeepingState(TypedDict, total=False):
    # —— 输入(由 channel 翻译后传入,channel 无关)——
    image_bytes: bytes      # 原图字节,用于 LLM 识别 + 上传 Bitable 附件
    user_id: str            # 发送者 open_id(渠道无关的字符串)
    request_id: str         # 幂等去重用(如 message_id)
    source: str             # 飞书 / 快捷方式
    on_progress: Optional[Callable[[str], None]]   # 可选流式回调(见 §11.3)
    # —— 中间产物 ——
    raw_output: Optional[str]     # LLM 返回的原始文本(交给 validate 解析)
    transaction: Optional[dict]   # 解析+校验后的 JSON
    retries: int                  # validate 失败重试计数(默认 0)
    validation_error: Optional[str]  # 上次校验错误,回喂给 analyze 修复用
    # —— 输出(交给 channel 去"发")——
    is_transaction: bool
    record_id: Optional[str]      # 写入 Bitable 后的记录 ID
    reply: Optional[dict]         # 结构化回复(channel 决定渲染成卡片还是文本)
    error: Optional[str]
```

> 注意:State 里**没有 chat_id / image_key / card_msg_id 这类飞书概念**。channel 自己保留这些,core 只管业务字段。`on_progress` 是唯一一个"行为类"字段,但它的实现仍然由 channel 提供——core 只是按约定调用它。

### 4.2 节点与流转

```
START
  │
  ▼
[analyze]          调用 GLM-4V(prompt.txt + image_b64)→ 拿到原始文本
  │
  ▼
[validate]         解析 JSON + Pydantic 校验
  │                  │
  │ 校验通过          │ 校验失败 且 retries < N
  │                  └──────────► 回到 [analyze](把错误回喂模型修复)
  │                                 失败且超过 N 次 → [build_error_reply]
  ▼
(条件分支) is_transaction?
  │                       │
  │ False                 │ True
  ▼                       ▼
[build_not_tx_reply] [write_pending]   写 Bitable(状态=待确认)+ 上传截图附件 → record_id
  │                       │
  │                       ▼
  │                 [build_confirm_card]  生成"识别结果+确认按钮"卡片数据(含 record_id)
  │                       │
  └──────────┬────────────┘
             ▼
            END  →  返回 reply 给 channel,由 channel 发送
```

- **结构化输出(关键)**:`GLM-4.6V` 经网关不支持 `response_format`,所以靠 `validate` 节点把关——解析失败/字段不合法时,通过条件边回到 `analyze`,并把校验错误拼进 prompt 让模型自我修复;`state` 里记 `retries`,超过上限走兜底回复。这就是用 LangGraph「条件边 + 循环」实现的规范化。
- **图片下载** 移到 channel 层(飞书特有 API),不进图;core 直接拿 `image_b64`。
- **错误处理**:其余节点可设 RetryPolicy;失败走统一 `build_error_reply` 兜底。
- **幂等**:channel 入口用 `request_id`(message_id)去重,避免重复记账。

### 4.3 各节点职责

| 节点 | 动作 | 输出 |
|------|------|------|
| `analyze` | 调 GLM-4V,prompt 用 `prompt.txt`;若 `validation_error` 非空则附带上次错误请模型修复 | `raw_output` |
| `validate` | 剥离 ```json 包裹 → `json.loads` → Pydantic 校验字段 | `transaction, is_transaction` 或 `validation_error`+`retries`+1 |
| `write_pending` | Bitable 新增行(状态=待确认)+ 上传截图附件 | `record_id` |
| `build_confirm_card` | 组装确认卡片数据(展示字段 + 确认按钮带 record_id) | `reply` |
| `build_not_tx_reply` | 组装"这看起来不是交易截图"文本回复 | `reply` |

---

## 5. Flow B:确认 与 修改

用户对那张待确认卡片有两种后续动作,channel 分别路由到 core 的两个入口:

### 5.1 确认(点卡片按钮)

```
卡片按钮回调 → 拿到 value 里的 record_id
  → storage 更新该行:状态=已确认,确认时间=now
  → 产出"已记账 ✅"回复
```
逻辑很短,core 里一个 `confirm(record_id)` 函数即可,不必做成图。

### 5.2 撤销(点卡片按钮)

```
卡片按钮回调 → 拿到 value 里的 record_id
  → storage 删除该行待确认记录
  → patch 原卡片为"已撤销",不再展示任何按钮
  → 移除 card→record 映射,后续回复不再触发修改
```
撤销只适用于待确认候选记录。若记录已确认,不删除 Bitable 行,卡片进入无按钮错误态并提示去多维表格处理;若记录已不存在,按撤销完成处理。

### 5.3 修改(回复消息)— 实际采用「失效旧卡 + 新卡」对话流

用户直接回复自然语言(如「金额改成 50」「这是交通不是餐饮」),channel 拿到回复事件后:
```
① 定位记录:回复事件带 parent_id(=我们发的卡片消息)→ 查 card→record 映射得 record_id
② 状态守卫:若 record 已是「已确认」,直接文本回复"请去多维表格自行修改"并退出
③ 抓 old_tx:从 Bitable 读出该行当前字段(留作失效卡片显示用)
④ 发新卡:reply_card(用户的修改消息id, pending_card) → 拿到 new_card_msg_id
⑤ LLM 合并(流式):core.actions.modify(record_id, user_text, on_progress=updater)
     - prompt 用 prompts/modify.md,$current_json + $user_text 双占位符
     - 流式 token 通过 on_progress 回调,channel 节流更新新卡片为 typing_card
     - 解析 + Pydantic 校验(失败重试)→ 写回 Bitable
⑥ 收尾(按 modify 返回值):
     - transaction_pending:新卡 → confirm_card(新值);旧卡 patch → invalidated_card(old_tx);
                          card 映射切换:_forget(old) → _remember(new → record_id)
     - no_modification:新卡 → no_modification_card;旧卡不动(因为根本没改 Bitable)
     - error:新卡 → error_card;旧卡不动
```

**关键不变量:** *只有真改了 Bitable 才动旧卡* —— 出错或意图不明时旧卡纹丝不动,用户继续操作不丢上下文。

**关于「是否需要新起会话」**:不需要。第 ⑤ 步是**一次性(one-shot)调用**,prompt 自带「当前 JSON + 修改文本」全部上下文,不依赖多轮对话记忆。也**不必去飞书拉父消息内容**——`parent_id` 只用来定位 `record_id`,字段值从 Bitable 读。

**`card_msg_id → record_id` 映射的实现**:进程内 `OrderedDict` LRU(上限 256 条)+ `threading.Lock`。MVP 决策(见 §9-1):进程重启会丢映射,旧卡片不再可修改;成本可接受,后续多实例部署再换共享存储。

### 5.3 已确认记录被回复

若该记录状态已是「已确认」,不再接受文本修改,直接回复:**「该记录已确认,请去多维表格中自行修改」**。

---

## 6. 飞书侧设计

### 6.1 多维表格字段

| 字段名 | 类型 | 来源 |
|--------|------|------|
| 商户 | 文本 | `transaction.merchant` |
| 商品 | 文本 | `transaction.goods` |
| 类别 | 单选(餐饮/交通/购物/娱乐/生活缴费/其他) | `transaction.category` |
| 金额 | 数字 | `transaction.amount`(转 float) |
| 状态 | 单选(待确认/已确认) | 程序写入 |
| 置信度 | 数字 | `transaction.confidence` |
| 录入时间 | 日期 | 写入时刻 |
| 确认时间 | 日期 | 确认时刻 |
| 用户 | 文本/人员 | 发送者 |
| 截图 | 附件 | 原图(必存) |
| 来源 | 单选(飞书/快捷方式) | 入口 channel |

> 已移除「原始消息ID」列(按反馈)。幂等去重改在程序内存/runtime 层处理,不落表。

> **关于 wiki 托管的表格(本期实际情况):** 本期使用的表格挂在知识库下,URL 形如 `https://xxx.feishu.cn/wiki/<wiki_node_token>?table=<table_id>&...`。`.env` 里的 `BITABLE_APP_TOKEN` 直接存的是这个 **wiki 节点 token**;Bitable API 实际需要的是 `obj_token`(=真正的 app_token),所以 `storage/bitable.py` 启动时要先调 `wiki/v2/spaces/get_node` 把 wiki token 解析成 `obj_token` 再缓存使用。

### 6.2 飞书开发者后台配置

- **事件订阅:** `im.message.receive_v1`(收消息);**卡片按钮回调**(card action / 卡片交互回传)。
- **权限 scope:**
  - 接收/发送消息(`im:message`、以机器人身份发消息)
  - 读取消息中的图片资源(下载 image)
  - 读写多维表格(`bitable:app`)
  - 上传素材/附件(供 Bitable 附件上传)
- **机器人:** 启用机器人能力,并加入要使用的群/单聊。
- **长连接:** 后台「事件订阅」选「使用长连接接收事件」。

---

## 7. GLM 调用(`src/llm/glm.py`)

```python
from openai import OpenAI
client = OpenAI(api_key=CONFIG.llm_api_key, base_url=CONFIG.llm_base_url)
```

**单一 `_chat_completions(messages, on_text=None)` 内核**:`on_text=None` → 常规调用;`on_text=callable` → `stream=True`,边接收边累积、每个 delta 调一次 `on_text(accumulated)`。两个公开包装:

- `call_vision(prompt, image_bytes, on_text=None)` — 识别。**调用前先 `_shrink_for_llm` 缩图**:Pillow 处理,最长边 1600px、JPEG quality 85,小于 300KB 且分辨率本来就低的图直接跳过编解码。实测原图 443KB → 138KB(~31%),LLM 耗时 16s → 9s。
- `call_text(prompt, on_text=None)` — 修改流程的纯文本调用,消息构造和模型一致,共享缩图/流式之外的全部基础设施。

**响应解析**:`extract_json(raw)` 剥离 ```json 包裹后 `json.loads`,交由 `core/schema.py` 的 Pydantic 模型做字段校验。模型不支持 `response_format`,所以"规范输出"靠 `core/graph.py` 的 `validate` 节点 + 失败回 `analyze` 重试。

**Prompts 在 `prompts/`(`.md` 文件 + `$var` 模板):**
- `prompts/recognize.md` — 识别 prompt
- `prompts/modify.md` — 修改合并 prompt,占位符 `$current_json` / `$user_text`,产物含 `is_modification` 字段
- 加载器 `src/prompts.load_prompt(name, **vars)`:`string.Template.safe_substitute`,选 `$var` 而非 `{var}` 是为了不和 prompt 里的 `{...}` JSON 示例冲突

---

## 8. 建议项目结构(多入口友好)

```
bookkeeping-agent/
├── .env                  # LARK_* / MODAL_* / BITABLE_* / WEB_*
├── prompts/
│   ├── recognize.md          # 识别 prompt
│   └── modify.md             # 修改合并 prompt($current_json / $user_text)
├── pyproject.toml            # langgraph / lark-oapi / openai / fastapi / uvicorn / pydantic / python-dotenv / pillow
├── TECH_DESIGN.md
├── README.md
└── src/
    ├── __init__.py           # logging.basicConfig(时间戳/level/模块名)
    ├── config.py             # CONFIG: 集中读 .env、必填字段校验
    ├── prompts.py            # load_prompt(name, **vars)
    ├── main.py               # 入口:飞书 WS 后台线程 + Webhook Uvicorn 主线程
    │
    ├── core/                 # 业务核心,渠道无关
    │   ├── schema.py         # Transaction / ModifyResult(Pydantic 校验 + 修复重试)
    │   ├── state.py          # BookkeepingState TypedDict(含 on_progress 流式回调)
    │   ├── graph.py          # Flow A LangGraph:analyze / validate / write / build_reply
    │   └── actions.py        # confirm() / modify() / is_confirmed() / get_transaction()
    │
    ├── llm/
    │   └── glm.py            # call_vision / call_text(共享 _chat_completions + 流式 + 缩图)
    │
    ├── storage/
    │   └── bitable.py        # 单例 bitable_client:wiki→obj_token 解析、CRUD、附件上传
    │
    └── channels/             # 入口适配层
        ├── feishu/
        │   ├── app.py        # ws 入口 + 事件分发 + 后台线程 + 幂等去重 + card map
        │   ├── client.py     # SDK 封装:download_image / send_card / upload_image / reply_card / update_card
        │   └── cards.py      # pending / typing / confirm / confirmed / invalidated / no_modification / not_transaction / error
        └── web/
            └── app.py        # FastAPI Webhook:multipart image + WEB_REVIEW_CHAT_ID + 202 accepted
```

> **关于 prompt 存储:** 用 `.md` 文件而不是嵌进 .py,理由是 ① 改 prompt 不动 Python、PR diff 干净 ② 不用处理 f-string 与 JSON `{}` 的转义冲突。模板变量用 `$name` 语法(`string.Template.safe_substitute`)而不是 `{name}`,这样 prompt 里可以放 `{...}` 形式的 JSON 示例。

> 加新入口时只需在 `channels/` 下加一个子包,实现"翻译入站事件 + 发送出站回复"两件事;`core/`、`llm/`、`storage/` 不动。

---

## 9. 已决事项 & 实际选择

**全部已落地:**
- 修改关联:回复 `parent_id` → `card_msg_id` → `record_id`(决策 #9)
- 修改后流程:**「失效旧卡 + 新卡」对话流**(决策 #10 在实施时演进,见 §5.3)
- 撤销流程:待确认卡片可删除候选记录,成功后卡片进入无按钮「已撤销」终态(§5.2)
- 已确认记录被回复:文本回复"请去多维表格自行修改"(§5.3)
- 修改用 LLM 合并:`prompts/modify.md` + `ModifyResult` Pydantic(决策 #5)
- 不支持 `response_format`,用 `validate` 节点 + 重试规范输出(#8)
- **映射持久化**:进程内 LRU(上限 256 条)+ `threading.Lock`。MVP 决定不落表,接受"进程重启 → 旧卡片不再可修改"的代价
- **修改话术边界**:`prompts/modify.md` 让模型返回 `is_modification` 字段,false 时新卡片显示「❓ 未识别修改」,旧卡不动

---

## 10. 实施步骤(已全部完成)

1. ✅ 修 `prompt.txt` 语法 → 迁移到 `prompts/recognize.md` + 新建 `prompts/modify.md`;`.env` 补 `BITABLE_*`;`pyproject.toml` 补 `openai/python-dotenv/pydantic/pillow`
2. ✅ `config.py`(frozen dataclass + 必填校验)+ `channels/feishu/client.py`(`download_image`)+ ws 长连接跑通,图片落 `downloads/`
3. ✅ `llm/glm.py` 跑通图片 → GLM-4.6V → JSON,加 magic-byte mime 检测和 ```json 兜底
4. ✅ `storage/bitable.py`:wiki → obj_token 解析(带 fallback)、字段映射、create/get/update/附件上传——其中踩了 3 个坑(scope / 协作者权限 / `file()` 要 BytesIO,见 §11)
5. ✅ `core/graph.py` LangGraph 串通:`analyze → validate → write → build_reply`,带条件边 + 重试循环(MAX_RETRIES=2)
6. ✅ 卡片确认 + 文本修改:`actions.confirm` / `actions.modify` + `feishu/cards.py` 8 种卡片 + card-action 回调
7. ✅ 联调 + 边界:非交易图(category 空 → 默认"其他"避免无谓重试)、事件幂等去重、修改意图兜底(`is_modification=false`)

## 11. 实施中追加的能力与踩坑记录

这里记录原始设计 §1-10 没有但实施中证明必须做的事,以及那些"文档不会写但下次必踩"的坑。

### 11.1 异步 handler + 幂等去重

**问题**:GLM 单次调用 10-16s,远超飞书 ws 事件 ack 超时(~3s),导致**飞书重发事件 → 重复进图 → 重复记账**。

**解法(两件事一起做)**:
1. **`_on_message` 同步部分只做解析/分发,heavy 路径丢 `threading.Thread`**(`_process_image` / `_process_modify` / `_process_confirm`)。handler 在 ms 级返回,飞书拿到 ack,不再重发。
2. **`message_id` LRU 幂等**(上限 1024 条,`threading.Lock` 保护)。即使飞书因为网络抖动重发,第二次进入直接 `[skip] duplicate`。

异步是消除重发的根本解,幂等是防御性补丁——两个必须共存。

### 11.2 图片缩放(`_shrink_for_llm`)

**问题**:手机截图常 1080×2400+、~400KB+ PNG,base64 后近 600KB,网络传输 + 模型处理都慢。

**解法**:Pillow 处理 → 长边超过 1600px 才缩 → JPEG quality 85。原图 443KB / 1179×2556 → 138KB / 738×1600(31% 体积),LLM 耗时 16s → 9s(~45% 提速),识别精度无损。

**重要**:**只在送 LLM 时缩**,Bitable 附件仍存原图,核对截图时能看清。

### 11.3 LLM 流式 + 卡片打字机

**问题**:用户等 10s 卡片不动,以为挂了。

**解法**:`openai` SDK `stream=True`,`call_vision/call_text` 增加可选 `on_text(accumulated)` 回调;channel 层用 `_StreamUpdater`(400ms 节流)在卡片上 patch `typing_card`,把 JSON 文本一段一段往里"打"。

架构上的关键:**`on_progress` 通过 `BookkeepingState` 透传给 `node_analyze`,core 只调用、不实现**。换 Web 入口要 SSE 推送时,只需要换 callback 的实现。

### 11.4 「失效旧卡 + 新卡」修改 UX(§5.2)

**原设计**:修改后 patch 旧卡片为新值,继续待确认。

**实施时改成**:旧卡变灰「🚫 已失效」+ 新卡作为对用户修改消息的回复挂出。理由:
- 形成对话流式的修改历史,用户能看到"我当时改的是这个"
- **只有真改了 Bitable 才动旧卡**(`no_modification` / `error` 时旧卡不动),用户没失败感

### 11.5 飞书 / 多维表格踩坑清单

| 现象 | 根因 | 解法 |
|------|------|------|
| `code=99991672` Access denied | scope 没开 | 后台开 `bitable:app` + `wiki:node:read` + 附件上传相关 scope |
| `code=1061004 forbidden` | scope 够了但 wiki 节点没把 bot 加为协作者 | 进 wiki 页面 → 添加协作者 → 选 bot → 「可编辑」 |
| `code=1062009 size inconsistent` | `UploadAllMediaRequestBody.file()` 传了 raw bytes | 必须传 `io.BytesIO(bytes)`(SDK 期望 file-like) |
| `code=200340` patch card 失败 | 卡片 config 没声明 `update_multi: true` | 所有 card config 加 `"update_multi": True` |
| Pydantic 重试 N 次都失败:`category ''` not in enum | 非交易图模型返回空 category | validator 中 `if not v: return "其他"`——空值静默归一,非法值仍 raise 触发重试 |

### 11.6 Wiki 节点 token vs Bitable obj_token

URL 形如 `/wiki/<token>?table=...` 时,**那个 token 是 wiki 节点 token,不是 bitable app_token**。多数 bitable API 实际需要 obj_token。`storage/bitable.py` 启动时调 `wiki/v2/spaces/get_node` 解析转换,失败则把输入当 obj_token 兜底(适配 `/base/` URL 直接给的情况)。

### 11.7 Prompt 文件 vs 嵌入 .py 常量

**决定**:用 `.md` 文件 + `string.Template` 加载。理由:
- 改 prompt 不动 Python、PR diff 干净、非工程师可改
- `prompts/modify.md` 里有 JSON 示例 `{ "is_modification": ... }`,如果用 `str.format`/f-string 这些 `{` 全要转义成 `{{`,极易出错
- `$var` 语法 `string.Template.safe_substitute` 跟 JSON `{}` 零冲突
```
