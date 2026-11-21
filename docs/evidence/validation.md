# 验证记录

这里区分原工程的真实结果和公开副本的验证范围。

| 范围 | 结果 |
| --- | --- |
| 原工程，UE 5.3.2 | 实际使用控件做过用途检查、两张样例批量导出、取消确认、拒绝旧目录、单张测试副本的 sRGB 修复与保存、资产包重载。后续独立复核也确认该副本重载后 sRGB 为 True。 |
| 原工程，离线模拟 | 用途与导出相关两组分别通过 42/42 和 75/75 用例。修复桥接相关通过 126/126 用例。使用模拟环境，不计为实时 UE 操作或公开版的新测试。 |
| 公开副本，离线 | 在新路径重新检出后，离线报告、运行目录边界与修复桥源码指纹检查通过。8 条合成记录生成 JSON 和两份 CSV，修改资产 0 项。蓝图中保存的四段 Python 脚本及其编译副本通过语法和模拟调用核对。 |
| 公开副本，UE 端到端 | 当前环境尝试启动本机 UE 5.3.2 命令行验证无有效引擎日志。公开副本的蓝图界面点击、原生确认对话框、实际修复保存和跨机器启动仍需在完整编辑器中复验。 |

- [同一纹理的 color 报告](audit-color/audit_report.json)与[normal 报告](audit-normal/audit_report.json)。
- [批量报告](audit-batch-normal/audit_report.json)、[问题清单](audit-batch-normal/issues.csv)及[建议清单](audit-batch-normal/suggestions.csv)。
- [一次成功修复的结果](repair-example/result.json)、[事件顺序](repair-example/events.jsonl)和[备份清单](repair-example/backup_manifest.json)。这些是原工程的历史记录。

## 补做公开副本验证

先按 [README](../../README.md) 完成一次实际界面流程。也可在 UE 的命令行中运行 [verify_release_ue.py](../../tools/verify_release_ue.py) 以检查蓝图加载、真实纹理加载、报告生成和预览。该脚本代入已保存样例作为选中资产，无须 UI 操作，只写入测试报告，不执行实际修复。完成时日志输出 `TA_RELEASE_SMOKE_OK`。

~~~powershell
# 在项目根目录，先让 $ueCmd 指向本机 UnrealEditor-Cmd.exe
& $ueCmd (Join-Path $PWD 'TextureAuditTool.uproject') '-run=pythonscript' "-script=$(Join-Path $PWD 'tools/verify_release_ue.py')" '-nullrhi' '-unattended' '-stdout'
~~~
