# ADR-0029：从用户级应用数据目录发现本机私有知识包

## 状态

已采纳

## 日期

2026-09-16

## 当时遇到了什么

[ADR-0028](0028-bind-user-domain-knowledge-as-read-only-snapshots.md)确定私有领域知识包是数据型只读内容，
由新 CodingRun 显式选择并绑定 state 快照；当时把仓库内已忽略的 `var/knowledge/private/` 作为默认作者目录，
且不扫描用户目录。继续确认日常管理方式后发现，这会把用户长期内容绑定到某个 clone/worktree：删除仓库、
切换 checkout 或清理 `var/` 都可能误伤本机知识，多仓库也无法自然复用。

Windows、Linux 和 macOS 已有不同的用户数据目录约定。项目若手写 `C:\Users\...`、`~/.local/share` 等路径，
会把平台差异和测试覆盖带进产品代码。与此同时，自动发现内容若等同于自动启用，又会让新增文件静默改变
模型输入，与 ADR-0028 的显式选择和可恢复绑定相冲突。

## 最后决定

- 使用 `platformdirs.PlatformDirs(appname="abaqus-sim-agent", appauthor=False).user_data_path` 解析用户级应用
  数据根；知识包安装根为其下的 `knowledge/packs/`。仓库 `var/` 不再是产品默认来源，只可由测试或显式宿主
  组合当作覆盖目录。
- 应用只发现这个专属根目录的直接非隐藏子目录，不扫描 workspace、用户主目录其他位置、网络位置或任意
  递归路径。每个候选目录必须有 strict `pack.json`，且目录名等于稳定 `pack_id`；文件系统和 manifest 就是
  首版本机清单，不增加数据库、远程 registry 或后台 watcher。
- “发现”与“启用”分开。安装或被列出不代表进入模型上下文；新 CLI 通过可重复的
  `--knowledge-pack <pack_id>`，Web 通过创建请求中的 `knowledge_pack_ids` 显式选择。默认列表为空，恢复期间
  不能增减或替换包。
- manifest 明确列出 UTF-8 文本资源、入口、作者 release、用途、兼容声明、来源和分项验证状态。宿主拒绝
  未列出文件、链接/junction、路径越界、重复身份、超限文件和不支持的资源类型；作者不填写 hash。
- 选择后仍沿用 ADR-0028：模型调用前把实际 bytes 快照到 workspace 外的 CodingRun state，launch 保存
  `pack_id`、release、逐文件 hash、pack digest、bundle digest 和 state 相对路径。每个工作段先校验快照，
  再物化仅含已声明资源的临时只读工具根；源包之后更新或消失不改变该 run。
- 模型只看到所选包的宿主生成短目录，通过 `knowledge_list_directory` / `knowledge_read_file` 按需读取。
  包内 `SKILL.md` 仍只是文本；包不能注册 instructions、工具、Shell、权限、预算或 verifier。

本决定只改变 ADR-0028 的默认作者位置和发现方式；数据型只读包、显式 run 选择、快照恢复绑定、来源隔离和
非分发边界继续有效。不建立跨机器安装协议、包市场、依赖求解、签名或自动语义选包。

## 为什么这样选

用户级应用数据目录让知识资产独立于源码 checkout，也符合本机单用户产品的所有权。只扫描应用专属根目录，
可以提供类似本地插件的可发现性，却不会扩大到任意文件系统搜索。把启用保留为每个 run 的显式动作，则使
新增、更新或隔离包不会静默改变正在运行或恢复中的模型输入。

`platformdirs` 统一平台路径语义；产品代码只拥有知识包根的解析和只读消费，包作者仍拥有正文。文件系统
manifest 足以支撑当前小规模本机使用，增加数据库或安装状态机不能改善内容质量和任务收益证据。

## 没有采用的方案

- 继续默认使用仓库 `var/knowledge/private/`：实现简单，但内容生命周期错误地跟随 checkout。
- 扫描整个用户目录或接受模型提供路径：发现范围不可审计，也会扩大隐私与路径攻击面。
- 发现即自动启用：新增文件会静默改变 prompt 和任务结果，破坏 run 级复现。
- 把包放进 Codex 的 `.codex`：生命周期属于另一个应用，卸载、缓存和权限所有权会混淆。
- 先建本机包数据库或全局版本解析器：当前 manifest 与不可变 run 快照已经覆盖身份、恢复和冲突检查。

## 带来的影响

- 有利：同一用户的多个 clone/worktree 共用私有知识包，源码仓库删除或切换不影响内容。
- 有利：CLI 与 Web 都能列出本机包，但只有创建 run 时的明确选择会进入 launch。
- 有利：源目录、run state 快照和临时模型可见副本的所有权分离，恢复不依赖源目录。
- 代价：增加 `platformdirs` 运行时依赖、launch v6、每 run 内容副本和启动前完整 hash 校验。
- 风险：本机私有只描述存储与分发；模型实际读取的内容仍可能发送给配置的远程 Provider。
- 风险：schema/hash/只读边界不能证明专业内容正确，真实模型采纳和物理收益仍需单独验证。

## 落实与确认

- 核心 schema、发现、快照、校验和临时物化：
  `src/abaqus_sim_agent/coding/domain_knowledge.py`。
- CLI：`--list-knowledge-packs` 与可重复 `--knowledge-pack`；Web 提供知识包列表、创建时选择和运行详情。
- launch v6 绑定选中 bundle；恢复只读取和校验 state 快照，不重新扫描用户目录。
- 离线测试覆盖目录发现、manifest/path 边界、未列出文件、内容 digest、源删除后的恢复、快照损坏、CLI、
  Web 以及模型经产品只读工具读取。它们不请求真实模型或 Abaqus，不构成专业内容收益或物理正确性证明。

## 相关文档

- [ADR-0028：只读内容快照绑定](0028-bind-user-domain-knowledge-as-read-only-snapshots.md)
- [知识接入与证据边界](../architecture/knowledge-access.md)
- [项目架构](../architecture/overview.md)
- [ADR-0005：可追溯地吸收与重构上游](0005-traceable-upstream-reuse.md)
- [ADR-0020：Harness StepPersistence 会话续跑](0020-resume-coding-runs-with-harness-step-persistence.md)
