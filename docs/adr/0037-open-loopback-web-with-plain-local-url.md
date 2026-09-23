# ADR-0037：用普通本机 URL 打开回环 Web 工作台

## 状态

已采纳，2026-09-23。用户确认采用 Pi Web 的本机启动方式，并要求检查其他明显交互偏差。

## 决定

- `abaqus-sim-agent-web` 继续固定监听 `127.0.0.1`。服务真正进入 started 状态后，CLI 默认打开
  `http://127.0.0.1:<port>/`；`--no-open` 显式关闭自动打开。
- 页面不再要求终端生成的 `#session=<token>` 链接，也不向用户显示“使用完整会话链接”的错误。
- 浏览器首次 GET `/api/v1/session` 时，宿主设置 HttpOnly、SameSite=strict、仅 `/api` 可见的进程内
  session cookie，并返回 CSRF token。后续 API 仍要求有效 cookie；写请求还要求同源、JSON 和 CSRF。
- 服务重启后，页面刷新会重新建立本地浏览器会话，不要求找回旧终端输出或旧 fragment。
- 当前 CLI 不开放非回环 hostname，因此不新增密码页、Basic Auth 或远程访问。若以后允许局域网或代理
  访问，必须另行决定传输加密、主机允许列表和认证合同，不能把本机默认直接外推。

## 为什么这样选

原实现把随机 bootstrap token 放在 URL fragment 中。直接输入普通地址、浏览器先于服务就绪打开，或服务
重启后 cookie 失效时，页面会要求用户回到终端寻找完整链接。这个步骤没有产品状态含义，也与 Pi Web 默认
在回环地址直接打开普通 URL 的行为不一致。

完全移除浏览器会话和 CSRF 会简化实现，但会让其他网页更容易向本机 API 发起写请求。当前方案把初始化
变成无感的同源握手，保留 REST 写入边界，同时去掉用户可见 token。

## 没有采用的方案

- 保留 fragment token 并只改善提示：仍要求用户管理一次性启动链接，服务重启体验不变。
- 默认无 cookie、无 CSRF：更接近最简本机服务器，但会放弃已有的同源写请求校验。
- 增加 hostname 与密码登录：Pi Web 支持该模式，但本项目当前只承诺本机单用户回环部署，没有远程需求。
- 在 Uvicorn 启动前立即调用浏览器：可能先打开尚未监听的地址，产生连接错误或重复标签页。

## 带来的影响

- OpenAPI 删除 `SessionInput` 和 `POST /api/v1/session`；`GET /api/v1/session` 成为浏览器初始化入口。
- CLI 的 `--open` 改为默认行为，显式选项改为 `--no-open`。
- 开发服务器和打包后的页面都使用普通 origin，不再需要复制终端 fragment。
- API 客户端若不先初始化 session，仍会收到 401；这是传输合同，不是面向页面用户的登录流程。

## 落实与确认

- [FastAPI 边界](../../src/abaqus_sim_agent/web/app.py)、[CLI](../../src/abaqus_sim_agent/web/cli.py)
- [前端 API client](../../webui/src/api/client.ts)、[入口状态](../../webui/src/App.tsx)
- [Web 边界测试](../../tests/test_web_boundaries.py)、[生命周期测试](../../tests/test_web_lifecycle.py)、
  [CLI 测试](../../tests/test_web_cli.py)

## 关系

扩展 [ADR-0025](0025-local-web-workbench-over-coding-run.md) 的本地部署与会话防护语义；不改变
[ADR-0034](0034-bind-selected-workspaces-for-web-runs.md) 的工作区边界、
[ADR-0035](0035-delete-web-conversation-records-and-keep-workspaces.md) 的删除语义或
[ADR-0036](0036-interrupt-web-work-segments-and-manage-background-jobs-separately.md) 的停止语义。
