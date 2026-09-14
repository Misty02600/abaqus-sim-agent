# 回答阶段证据A/B v1：输入已冻结，模型调用被余额限制阻断

状态：**无有效模型答案，尚不能判断A/B效果。**

三个原任务各准备A/B两组，共六份输入。A保留历史实际送达的文档片段/API卡内容及未找到记录；B使用核对后的原文或版本化类型资料，不含评分提示、原答案或人工结论模板。两组的用户任务、初始文件、共同历史领域Skill、输出适配指令、输出schema和模型设置相同。

这是回答阶段模拟：输出拟写的`analysis.json`及必要时的拟修正脚本，不实际编辑原工作区，不提供文件、搜索、Shell或Abaqus工具。它不是历史Harness完整工具循环回放。

## 冻结设置

- OpenCode Go / `deepseek-v4-flash`，thinking disabled，temperature 0。
- 每项只尝试一次，HTTP retries=0；六次HTTP尝试上限，无失败补位。
- 最大输出3000tokens，输入/输出usage上限30000/4500；单次HTTP超时90秒，外层超时100秒。
- 顺序：API A/B、Riks B/A、warning A/B，彼此无会话历史共享。
- 判定使用既有9项必答事实与7类条件性主张检查；不向模型显示这些判定规则。

## 实际结果

六次HTTP请求均被网关以401 `CreditsError: Insufficient balance`拒绝，零成功模型答案。SDK记录的requests/input/output usage均为0；这描述SDK观测，不替代独立账单核对。

没有模型内容可供比较，不能记录为A或B的语义失败，也不能算作0/3任务通过率。未更换模型/渠道、未自动充值、未重跑或改写输入。

## 保留的工件

- `var/audits/answer-evidence-ab-v1/manifest.json`：输入、指令、来源、schema、执行顺序及实现hash；SHA-256 `eb19a0917b68afaa897a7aa98f96d783133b77448a93621029a2863c25d2e123`。
- 同目录六个`*-input.json`和`instructions.txt`：已冻结的真实模型输入。
- 六个子目录下的`run.json`/`messages.json`以及`run-summary.json`：请求拒绝记录。没有`answer.json`。
- 一次性驱动：`var/audits/run_answer_evidence_ab_v1.py`。

充值或获得明确的新渠道授权后，后续尝试应使用新执行目录并原样复用这批输入，保留本次拒绝记录；不得删除`execution-started.json`后将补跑伪装成首次执行。渠道或模型若变化，必须另记profile，A/B仍须使用相同设置。
