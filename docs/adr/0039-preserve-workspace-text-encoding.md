# ADR-0039：为 Abaqus 工作区文本保留 UTF-8/CP936 编码

## 状态

已采纳（2026-09-23）。用户确认不等待上游 issue，在项目内实现编码保持的文件能力。

## 背景

Abaqus 2024 在中文 Windows 上会以系统 `mbcs`/CP936 生成 `.rpy`、`.rec`、部分 `.inp` 和普通探针文本。
runner 对进程 stdout/stderr 已保留 raw bytes，并能在 UTF-8 失败后按 CP936 解码；问题发生在模型随后使用
Harness 文件工具时：`read_file`/`search_files` 把所有文本按 UTF-8 replacement 解码，`edit_file` 又要求
严格 UTF-8。历史 v6–v11 因此出现乱码读取和一次确定的非 UTF-8 编辑失败。Web 预览也把所有文本声明为
UTF-8。

文件系统没有可靠的逐文件“实际编码”属性；只有 BOM、Python coding cookie、runtime locale 和严格解码
结果可作为证据。概率检测和把编码选择交给模型都会扩大不确定性。

## 决定

- 项目用 `AbaqusTextFileSystem` 替换 `Coder` 默认 `FileSystem`，继续复用 Harness 的工作区归属、symlink、
  allow/deny/protected patterns、事件和边界控制。实现与固定 `pydantic-ai-harness==0.31.0` 绑定；不修改依赖
  安装目录，也不全局 monkeypatch 其他 capability。
- 文本策略只支持 UTF-8 与 CP936：先处理 UTF-8 BOM 和 Python 前两行 coding cookie；`mbcs`/`gbk`/
  `gb2312` 映射到本机 Abaqus runtime 的 CP936；无声明时严格 UTF-8，再严格 CP936。两者都失败或声明其他
  编码时明确拒绝，不以 replacement character 伪造内容。
- `read_file` 显示 `encoding`，`search_files` 为命中文件报告编码，`file_info` 报告编码或 unsupported。
  文件 hash 绑定原始 bytes。
- `edit_file` 和覆盖已有文件的 `write_file` 保留原编码；新文件使用 UTF-8。新文本不能按原编码无损表示、
  expected hash 过期或文件不是支持文本时，在截断写入前拒绝，原文件保持不变。
- Web 文本预览复用同一策略，在 2 MiB 既有边界内解码后统一返回 UTF-8；图像仍使用原始 FileResponse。
- Abaqus runner、raw log、runtime tool 的 `stdout/stderr` 与 encoding 字段不变。本决定不增加通用编码发现、
  不支持 UTF-16，也不改变二进制文件行为。
- launch 升到 v9 并绑定 `utf8-cp936-preserve-v1`；旧开发期 launch 明确拒绝，不把旧 replacement-view hash
  解释为当前 raw-byte hash。

## 取舍与验证

项目子类仍使用 Harness 0.31.0 的部分实现接口，升级依赖时必须重新审查。上游 2026-09-22 合并的
`open_read/open_write` 尚未进入当前 pin，且不覆盖 `read_file/search_files` 解码，因此不能单独解决本问题。
若未来上游提供统一 codec policy，删除项目子类并迁移到公开配置。

离线测试覆盖 UTF-8、`mbcs`/CP936、无声明 `.inp`、不支持编码、中文搜索、读取/编辑/hash 闭环、写回编码、
不可表示字符与 stale hash 不改文件、Coder 真实工具组合和 Web 预览。历史 raw run 不回填，本次不调用真实
Provider 或 Abaqus。
