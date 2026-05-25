# 记账 Agent

> 通过飞书发送订单截图,多模态 LLM 自动识别交易信息,写入飞书多维表格;支持卡片确认 + 自然语言修改。

## 功能特性

- 📸 **图片识别** —— 发送支付/订单截图(微信、支付宝等),自动提取商户、商品、类别、金额、置信度
- ⌨️ **流式打字机** —— LLM token 流式输出,卡片每 400ms patch 一次,看得见识别进度
- 📋 **两阶段确认** —— 先「📋 待确认」+ 原图存档,点「✅ 确认」后置「✅ 已记账」
- ✏️ **自然语言修改** —— 回复卡片「金额改成 50」「类别改为交通」,LLM 合并后旧卡片标记「🚫 已失效」,新卡片挂在你的修改消息下面
- 🛡️ **健壮性** —— Pydantic schema 校验 + 失败重试、`message_id` 幂等去重、handler 立即 ack(避免飞书超时重发)
- 🔌 **多入口架构** —— 飞书图片入口 + Apple 快捷方式 Webhook 入口,业务核心 `core/` 完全不认识渠道

## 技术栈

- **[LangGraph](https://langchain-ai.github.io/langgraph/) 1.x** —— 业务编排,`analyze → validate → write → reply`,validate 失败回 analyze 重试(repair loop)
- **GLM-4.6V**(智谱) —— 多模态识别 + 文本修改合并,经 OpenAI 兼容网关调用
- **[lark-oapi](https://github.com/larksuite/oapi-sdk-python)** —— 飞书 SDK,WebSocket 长连接接收事件
- **FastAPI + Uvicorn** —— Apple 快捷方式 Webhook,接收截图后异步进入识别流程
- **Pydantic 2** —— schema 校验,自动触发 LLM 修复重试
- **Pillow** —— 图片缩放(送 LLM 前缩到 1600px JPEG,实测 ~50% 提速)

## 快速开始

### 1. 安装

```bash
uv sync
```

### 2. 配置 `.env`

```env
# 智谱 GLM(OpenAI 兼容网关)
MODAL_API_KEY=<your-key>
MODAL_APP_NAME=GLM-4.6V
MODAL_APP_BASE=https://open.bigmodel.cn/api/paas/v4

# 飞书应用
LARK_APP_ID=cli_xxx
LARK_APP_SECRET=xxx

# 飞书多维表格(URL 形如 https://xxx.feishu.cn/wiki/<token>?table=<table_id>)
BITABLE_APP_TOKEN=<wiki-node-token-or-app-token>
BITABLE_TABLE_ID=tbl_xxx

# Apple 快捷方式 Webhook
WEB_HOST=0.0.0.0
WEB_PORT=8000
```

### 3. 飞书后台配置

应用需要:
- ✅ 启用机器人能力,加入要使用的群/单聊
- ✅ **事件订阅** → 选「长连接」→ 订阅 `im.message.receive_v1` + 卡片回传
- ✅ **权限 scope**:`im:message`、机器人发消息、读消息中的图片、`bitable:app`、`wiki:node:read`、附件上传相关
- ✅ 若多维表格挂在知识库下:把 bot **加为该 wiki 节点的协作者**(权限选「可编辑」)
- ✅ 在多维表格里**手动建好这些字段**:
   `商户`(文本)/ `商品`(文本)/ `类别`(单选:餐饮/交通/购物/娱乐/生活缴费/其他)/ `金额`(数字)/ `状态`(单选:待确认/已确认)/ `置信度`(数字)/ `录入时间`(日期)/ `确认时间`(日期)/ `用户`(人员)/ `截图`(附件)/ `来源`(单选:飞书/快捷方式)

### 4. 启动

```bash
uv run python -m src.main
```

向机器人发一张订单截图,~1 秒内出现「🔍 识别中…」卡片,5-15 秒后变成「📋 待确认」卡片(带「✅ 确认」按钮)。

Apple 快捷方式入口:

```http
POST /webhook/order-screenshot
Content-Type: multipart/form-data
```

表单字段:
- `image`:截图文件
- `WEB_REVIEW_CHAT_ID`:识别结果要发送到的飞书会话 `chat_id`

接口成功接收后立即返回 `202 Accepted`,只代表图片已被接收;识别中、待确认、失败、非交易截图都会反馈到请求携带的 `WEB_REVIEW_CHAT_ID` 对应飞书会话。快捷方式来源的确认卡片会展示截图缩略图,飞书图片来源不额外展示截图。

## 部署到腾讯云

仓库内已包含 GitHub Actions 部署模板:每次 push 到 `main` 会 SSH 到服务器,拉取最新代码,执行 `uv sync --frozen`,然后重启 `systemd` 服务。

### 1. 服务器首次初始化

以下命令只需要在腾讯云服务器上执行一次:

```bash
sudo mkdir -p /opt
sudo chown -R "$USER:$USER" /opt
cd /opt
git clone <your-github-repo-url> bookkeeping-agent
cd bookkeeping-agent

# 安装 uv,如果服务器已经有 uv 可跳过
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --frozen
```

把生产环境变量放到服务器的 `/etc/bookkeeping-agent.env`:

```env
MODAL_API_KEY=<your-key>
MODAL_APP_NAME=GLM-4.6V
MODAL_APP_BASE=https://open.bigmodel.cn/api/paas/v4
LARK_APP_ID=cli_xxx
LARK_APP_SECRET=xxx
BITABLE_APP_TOKEN=<wiki-node-token-or-app-token>
BITABLE_TABLE_ID=tbl_xxx
WEB_HOST=0.0.0.0
WEB_PORT=8000
```

安装并启动 `systemd` 服务:

```bash
sudo cp deploy/bookkeeping-agent.service.example /etc/systemd/system/bookkeeping-agent.service
sudo sed -i "s/User=ubuntu/User=$USER/" /etc/systemd/system/bookkeeping-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now bookkeeping-agent
sudo systemctl status bookkeeping-agent
```

### 2. 配置 GitHub Secrets 和 Variables

在 GitHub 仓库进入 `Settings` → `Secrets and variables` → `Actions`。

添加这些 `Secrets`:

- `TENCENT_HOST`:服务器公网 IP 或域名
- `TENCENT_USER`:SSH 用户名,例如 `ubuntu` 或 `root`
- `TENCENT_PORT`:SSH 端口,通常是 `22`
- `TENCENT_SSH_KEY`:用于登录服务器的私钥内容

建议添加这些 `Variables`:

- `PROJECT_DIR`:`/opt/bookkeeping-agent`
- `SERVICE_NAME`:`bookkeeping-agent`

### 3. 配置 SSH key

在本机生成一把专门给 GitHub Actions 用的部署 key:

```bash
ssh-keygen -t ed25519 -C "github-actions-bookkeeping-agent" -f ./bookkeeping-agent-deploy-key
```

把公钥追加到服务器:

```bash
ssh-copy-id -i ./bookkeeping-agent-deploy-key.pub <user>@<server-ip>
```

把私钥文件 `./bookkeeping-agent-deploy-key` 的完整内容填到 GitHub Secret `TENCENT_SSH_KEY`。

之后 push 到 `main` 即会自动部署;也可以在 GitHub Actions 页面手动点 `Deploy` → `Run workflow`。

常用排错命令:

```bash
sudo journalctl -u bookkeeping-agent -f
sudo systemctl status bookkeeping-agent
```

## 使用流程

```
发送截图 / 快捷方式上传截图
  ↓
[🔍 识别中…]  ← 飞书入口回复原消息;快捷方式入口发送到确认群
  ↓ 流式打字机(JSON 一段一段往里"打")
  ↓
[📋 待确认]  「商户:XX  金额:¥51  类别:餐饮 ...」+ [✅ 确认] 按钮
  │
  ├── 点 [✅ 确认]
  │     ↓
  │   [✅ 已记账]
  │
  └── 回复「金额改成 50」
        ↓
      旧卡 → [🚫 已失效]  (灰色,显示原值)
      新卡 ← [📋 待确认  金额:¥50]  (挂在你的回复下面)

      若回复内容 LLM 判断不是修改意图(如「好的」):
        旧卡不动,新卡显示 [❓ 未识别修改]
```

## 项目结构

```
src/
├── config.py             # 集中配置(读 .env,启动即校验)
├── prompts.py            # load_prompt() — 加载 prompts/*.md + 替换 $var
├── main.py               # 入口,启动飞书长连接 + Webhook 服务
├── core/                 # 业务核心,完全不认识飞书
│   ├── schema.py         # Pydantic Transaction / ModifyResult
│   ├── state.py          # BookkeepingState TypedDict
│   ├── graph.py          # LangGraph Flow A
│   └── actions.py        # confirm() / modify() — 渠道无关
├── llm/
│   └── glm.py            # call_vision / call_text + 流式 + 缩图
├── storage/
│   └── bitable.py        # 多维表格 CRUD + 附件上传 + wiki→obj_token 解析
└── channels/
    ├── feishu/
    │   ├── app.py        # ws 入口 + 事件分发 + 后台线程 + 幂等
    │   ├── client.py     # SDK 封装
    │   └── cards.py      # 交互卡片 JSON 构造器
    └── web/
        └── app.py        # FastAPI Webhook: Apple 快捷方式上传截图
```

设计文档与踩坑记录详见 [`TECH_DESIGN.md`](./TECH_DESIGN.md)。
