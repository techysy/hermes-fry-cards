# 测试报告目录

本目录用于存放自动化测试报告与人工复盘记录。

## 约定

- 统一使用 Markdown 文件保存结果
- 可按日期命名，例如：`2026-08-23.md`
- 如有 HTML 报告，也可保存在同目录下
- 报告中需包含：测试命令、执行时间、通过/失败数量、关键回归项

## 当前状态

- 方案文档入口：`../../OPTIMIZATION_PLAN.md`
- 项目主说明：`../../README.md`

## 示例模板

```md
# 测试报告

## 执行时间
- 2026-08-23

## 测试命令
- pytest tests/ -q

## 结果
- 通过：X
- 失败：Y
- 跳过：Z

## 关键回归
- session cleanup
- flush throttle
- card fallback
```
