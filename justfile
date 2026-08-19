set minimum-version := "1.56.0"

[windows]
set shell := ["pwsh", "-NoProfile", "-CommandWithArgs"]

set default-list
set lazy

uv := require("uv")
uvx := require("uvx")
git := require("git")
clean_update_worktree := assert(git != "") + (if shell("git status --porcelain --untracked-files=normal") == "" { "" } else { error("update-template 需要干净的工作树（含未跟踪文件）") })

# 同步所有开发依赖组
[group("development")]
sync:
    {{ assert(uv != "") }}uv sync --all-groups

# 运行项目测试
[group("quality")]
test:
    {{ assert(uv != "") }}uv run pytest

# 检查 Ruff lint 和格式
[group("quality")]
lint:
    {{ assert(uv != "") }}uv run ruff check .
    uv run ruff format --check .

# 应用 Ruff 可修复规则和格式化
[group("quality")]
format:
    {{ assert(uv != "") }}uv run ruff check --fix .
    uv run ruff format .

# 运行 BasedPyright 类型检查
[group("quality")]
check:
    {{ assert(uv != "") }}uv run basedpyright

# 在干净工作树中使用官方 Copier 更新模板，再完成 lock 与质量验证
[group("maintenance")]
update-template:
    {{ clean_update_worktree }}{{ assert(uvx != "") }}uvx --from "copier>=9.17,<10" copier update --skip-answered
    just finish-template-update

# 完成已应用的模板更新；也用于解决 Copier 冲突或接手 Renovate PR 后继续验证
[group("maintenance")]
finish-template-update:
    {{ assert(uv != "") }}{{ assert(git != "") }}git diff --check
    git diff --cached --check
    uv run --no-project python -c "import subprocess, sys; result = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '--', '*.rej'], capture_output=True, text=True, check=True); rejects = result.stdout.splitlines(); sys.exit('Copier 更新留下冲突文件: ' + ', '.join(rejects)) if rejects else None"
    uv lock
    uv sync --locked --all-groups
    uv run --no-sync prek run --all-files
    uv run --no-sync ruff check .
    uv run --no-sync ruff format --check .
    uv run --no-sync basedpyright
    uv run --no-sync pytest

# 安装 pre-commit 和 commit-msg hooks
[group("hooks")]
hooks:
    {{ assert(uv != "") }}uv run prek install

# 更新 hook revisions，并保留七天 cooldown
[group("hooks")]
update-hooks:
    {{ assert(uv != "") }}uv run prek update --cooldown-days 7

# 预览最近版本的 changelog
[group("release")]
changelog:
    {{ assert(uv != "") }}uv run git-cliff --latest

# 在 main 上创建版本提交和 annotated tag，并整体原子推送到 origin
[group("release")]
bump:
    {{ assert(uv != "") }}{{ assert(git != "") }}{{ if shell("git branch --show-current") == "main" { "" } else { error("bump 只能在 main 分支执行") } }}uv run cz bump --yes
    git push --atomic --follow-tags origin HEAD
